"""Command-line interface: `segsense fix prog.c`, `segsense models`, `segsense serve`."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from .agent import Config, SegSense
from .llm import DEFAULT_PATCH_MODEL, DEFAULT_TRIAGE_MODEL, LLMError, TokenFactory, is_nvidia_model

_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def print_event(kind: str, data: dict) -> None:
    n = data.get("attempt")
    tag = _c("2", f"[{'orig' if n == 0 else f'try {n}'}]") if n is not None else _c("2", "[segsense]")
    if kind == "start":
        print(f"{tag} {data['file']} | compiler {data['compiler']} | patch model {data['patch_model']}")
    elif kind == "build":
        print(f"{tag} compiling with -fsanitize=address,undefined")
    elif kind == "build_failed":
        print(f"{tag} {_c('31', 'compile error')}\n{_indent(data['output'])}")
    elif kind == "crash":
        print(f"{tag} {_c('31;1', data['headline'])}  {data['summary']}")
        for frame in data["frames"]:
            print(f"        {frame}")
    elif kind == "pass":
        print(f"{tag} {_c('32;1', 'clean run')} (exit {data['exit_code']})")
    elif kind == "triage_start":
        print(f"{tag} triaging with {data['model']} ...")
    elif kind == "triage":
        print(f"{tag} triage ({data['seconds']}s): {data['text']}")
    elif kind == "patch_start":
        print(f"{tag} asking {data['model']} for a patch ...")
    elif kind == "patch":
        print(f"{tag} patch received in {data['seconds']}s ({data['tokens']} tokens)")
        if data["root_cause"]:
            print(f"        root cause: {data['root_cause']}")
    elif kind == "clean":
        print(f"{tag} {_c('32', data['message'])}")
    elif kind == "error":
        print(f"{tag} {_c('33', data['message'])}")
    elif kind == "fixed":
        print(f"\n{_c('32;1', 'Fixed')} after {data['attempt']} attempt(s).\n")
    elif kind == "gave_up":
        print(f"\n{_c('31;1', 'Gave up')} after {data['attempts']} attempts.")


def _indent(text: str, pad: str = "        ") -> str:
    return "\n".join(pad + line for line in text.splitlines()[-25:])


def _colour_diff(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(_c("32", line))
        elif line.startswith("-") and not line.startswith("---"):
            out.append(_c("31", line))
        elif line.startswith("@@"):
            out.append(_c("36", line))
        else:
            out.append(line)
    return "\n".join(out)


def cmd_fix(ns: argparse.Namespace) -> int:
    src = Path(ns.file)
    source = src.read_text()
    config = Config(
        patch_model=ns.model,
        triage_model=None if ns.no_triage else ns.triage_model,
        max_attempts=ns.max_attempts,
        args=shlex.split(ns.args) if ns.args else [],
        stdin=Path(ns.stdin).read_text() if ns.stdin else None,
        expected_stdout=Path(ns.expect_stdout).read_text() if ns.expect_stdout else None,
        timeout=ns.timeout,
        cc=ns.cc,
    )
    agent = SegSense(TokenFactory(), config, on_event=print_event)
    result = agent.fix(source, src.name)

    if result.success and result.final != result.original:
        print(_colour_diff(result.diff))
        if result.explanation:
            print("\n" + result.explanation)
        out = src if ns.in_place else Path(ns.output or src.with_name(src.stem + ".fixed.c"))
        out.write_text(result.final)
        print(f"\nWrote {out}")
    return 0 if result.success else 1


def cmd_models(ns: argparse.Namespace) -> int:
    models = TokenFactory().list_models()
    for m in models:
        if ns.all or "nemotron" in m.lower() or m.lower().startswith("nvidia/"):
            print(m)
    return 0


def cmd_serve(ns: argparse.Namespace) -> int:
    from .web import serve

    serve(ns.host, ns.port, ns.model, ns.triage_model)
    return 0


def main(argv: list[str] | None = None) -> int:
    patch_default = os.environ.get("SEGSENSE_PATCH_MODEL", DEFAULT_PATCH_MODEL)
    triage_default = os.environ.get("SEGSENSE_TRIAGE_MODEL", DEFAULT_TRIAGE_MODEL)

    p = argparse.ArgumentParser(prog="segsense", description="Agentic debugger for C memory bugs, powered by NVIDIA Nemotron on Nebius Token Factory.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fix", help="find and fix memory bugs in a C file")
    f.add_argument("file")
    f.add_argument("--args", help="command-line arguments for the program, as one quoted string")
    f.add_argument("--stdin", help="file to feed the program on stdin")
    f.add_argument("--expect-stdout", help="file with the exact stdout a correct run must produce")
    f.add_argument("--max-attempts", type=int, default=4)
    f.add_argument("--timeout", type=float, default=10.0, help="seconds per run")
    f.add_argument("--model", default=patch_default, help=f"patch model (default {patch_default})")
    f.add_argument("--triage-model", default=triage_default, help=f"fast triage model (default {triage_default})")
    f.add_argument("--no-triage", action="store_true", help="skip the fast triage call")
    f.add_argument("--cc", help="compiler to use (default: clang, then gcc)")
    f.add_argument("-o", "--output", help="where to write the fixed file (default: <name>.fixed.c)")
    f.add_argument("--in-place", action="store_true", help="overwrite the input file")
    f.set_defaults(func=cmd_fix)

    m = sub.add_parser("models", help="list NVIDIA models available on Token Factory")
    m.add_argument("--all", action="store_true", help="list every model, not just NVIDIA ones")
    m.set_defaults(func=cmd_models)

    s = sub.add_parser("serve", help="run the web demo")
    s.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    s.add_argument("--model", default=patch_default)
    s.add_argument("--triage-model", default=triage_default)
    s.set_defaults(func=cmd_serve)

    ns = p.parse_args(argv)
    for model in filter(None, [getattr(ns, "model", None), getattr(ns, "triage_model", None)]):
        if not is_nvidia_model(model):
            print(f"segsense: warning: {model} is not an NVIDIA model; SegSense is built to run on Nemotron.", file=sys.stderr)
    try:
        return ns.func(ns)
    except LLMError as exc:
        print(f"segsense: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
