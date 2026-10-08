"""The SegSense loop: build with sanitizers, run, ask Nemotron for a patch, re-test."""

from __future__ import annotations

import difflib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .llm import ChatResult
from .sanitizer import (
    BuildResult,
    RunResult,
    SanitizerReport,
    compile_source,
    find_compiler,
    run_binary,
)

SYSTEM_PROMPT = """You are SegSense, an expert C debugger.
You receive a C source file and the AddressSanitizer / UndefinedBehaviorSanitizer / LeakSanitizer
report it produced. Find the root cause and fix it with the smallest correct change.

Rules:
- Fix the real bug (bounds, lifetime, ownership, initialisation, overflow). Never hide it by deleting
  the failing code, disabling checks, or exiting early.
- Keep the program's intended behaviour and output the same.
- Free every allocation the program owns before it exits.
- Use only standard C and the headers already available.

Answer in exactly this format:
ROOT CAUSE: <one or two sentences>
FIX: <one or two sentences>
```c
<the complete corrected source file>
```"""

TRIAGE_PROMPT = """You triage C crash reports. In at most three short sentences, say what kind of
memory bug this is, the exact line responsible, and why it happens. No code."""

_CODE_BLOCK = re.compile(r"```(?:c|C|c99|c11)?\s*\n(.*?)```", re.S)
_FIELD = re.compile(r"^(ROOT CAUSE|FIX):\s*(.+)$", re.M)


class ChatModel(Protocol):
    def chat(self, model: str, messages: list[dict], temperature: float = ..., max_tokens: int = ...) -> ChatResult: ...


EventHandler = Callable[[str, dict], None]


@dataclass
class Attempt:
    number: int
    source: str
    build: BuildResult
    run: RunResult | None
    root_cause: str = ""
    fix: str = ""

    @property
    def passed(self) -> bool:
        return self.build.ok and self.run is not None and self.run.clean

    def failure_summary(self) -> str:
        if not self.build.ok:
            return "It did not compile:\n" + self.build.output[-2000:]
        assert self.run is not None
        if self.run.timed_out:
            return "It hung and was killed after the timeout."
        if self.run.report:
            return "It still failed:\n" + self.run.report.raw[:2500]
        return f"It exited with code {self.run.exit_code}."


@dataclass
class FixResult:
    success: bool
    original: str
    final: str
    attempts: list[Attempt] = field(default_factory=list)
    initial_report: SanitizerReport | None = None
    triage: str = ""

    @property
    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.original.splitlines(keepends=True),
                self.final.splitlines(keepends=True),
                fromfile="original.c",
                tofile="fixed.c",
            )
        )

    @property
    def explanation(self) -> str:
        last = next((a for a in reversed(self.attempts) if a.root_cause or a.fix), None)
        if not last:
            return ""
        return f"Root cause: {last.root_cause}\nFix: {last.fix}".strip()


@dataclass
class Config:
    patch_model: str
    triage_model: str | None
    max_attempts: int = 4
    args: list[str] = field(default_factory=list)
    stdin: str | None = None
    expected_stdout: str | None = None
    timeout: float = 10.0
    cc: str | None = None
    extra_cflags: list[str] = field(default_factory=list)


class SegSense:
    def __init__(self, llm: ChatModel, config: Config, on_event: EventHandler | None = None):
        self.llm = llm
        self.config = config
        self.emit = on_event or (lambda kind, data: None)
        self.cc = find_compiler(config.cc)

    def fix(self, source: str, filename: str = "program.c") -> FixResult:
        filename = Path(filename).name or "program.c"
        with tempfile.TemporaryDirectory(prefix="segsense-") as tmp:
            work = Path(tmp)
            self.emit("start", {"file": filename, "compiler": self.cc, "patch_model": self.config.patch_model})

            first = self._evaluate(work, filename, source, number=0)
            result = FixResult(success=first.passed, original=source, final=source, attempts=[first])
            result.initial_report = first.run.report if first.run else None

            if first.passed:
                self.emit("clean", {"message": "No sanitizer errors: nothing to fix."})
                return result

            if result.initial_report and self.config.triage_model:
                result.triage = self._triage(source, filename, result.initial_report)

            current = first
            note = ""
            for n in range(1, self.config.max_attempts + 1):
                patched, root_cause, fix = self._propose(source, filename, result, n, note)
                note = ""
                if patched is None:
                    self.emit("error", {"attempt": n, "message": "Model reply had no C code block; retrying."})
                    note = "Your previous reply had no ```c code block. Reply in the required format."
                    continue

                current = self._evaluate(work, filename, patched, number=n)
                current.root_cause, current.fix = root_cause, fix
                result.attempts.append(current)

                if current.passed:
                    result.success, result.final = True, patched
                    self.emit("fixed", {"attempt": n, "diff": result.diff, "explanation": result.explanation})
                    return result

            result.final = current.source
            self.emit("gave_up", {"attempts": self.config.max_attempts})
            return result

    def _evaluate(self, work: Path, filename: str, source: str, number: int) -> Attempt:
        src = work / filename
        binary = work / f"bin{number}"
        src.write_text(source)

        self.emit("build", {"attempt": number})
        build = compile_source(src, binary, self.cc, self.config.extra_cflags)
        if not build.ok:
            self.emit("build_failed", {"attempt": number, "output": build.output[-3000:]})
            return Attempt(number, source, build, None)

        self.emit("run", {"attempt": number})
        run = run_binary(binary, self.config.args, self.config.stdin, self.config.timeout, cwd=work)
        attempt = Attempt(number, source, build, run)

        if run.clean and self.config.expected_stdout is not None and run.stdout != self.config.expected_stdout:
            # Treat a behaviour change as a failure so the model cannot "fix" by gutting the program.
            run.report = SanitizerReport(
                "OutputCheck",
                "unexpected stdout",
                "program output differs from the expected output",
                raw=f"Expected stdout:\n{self.config.expected_stdout}\nActual stdout:\n{run.stdout}",
            )

        if run.report:
            self.emit(
                "crash",
                {
                    "attempt": number,
                    "headline": run.report.headline,
                    "summary": run.report.location(filename),
                    "frames": list(dict.fromkeys(f"{f.function} at {filename}:{f.line}" for f in run.report.user_frames(filename)))[:6],
                    "report": run.report.raw,
                },
            )
        elif run.timed_out:
            self.emit("crash", {"attempt": number, "headline": "Timeout", "summary": "program hung", "frames": [], "report": ""})
        else:
            self.emit("pass", {"attempt": number, "exit_code": run.exit_code, "stdout": run.stdout[-2000:]})
        return attempt

    def _triage(self, source: str, filename: str, report: SanitizerReport) -> str:
        self.emit("triage_start", {"model": self.config.triage_model})
        try:
            reply = self.llm.chat(
                self.config.triage_model,
                [
                    {"role": "system", "content": TRIAGE_PROMPT},
                    {"role": "user", "content": f"{_numbered(source, filename)}\n\nReport:\n{report.raw[:4000]}"},
                ],
                temperature=0.1,
                max_tokens=4096,
            )
        except Exception as exc:  # triage is a hint; never let it stop the fix
            self.emit("error", {"message": f"Triage failed: {exc}"})
            return ""
        self.emit("triage", {"model": reply.model, "text": reply.text, "seconds": round(reply.seconds, 1)})
        return reply.text

    def _propose(self, original: str, filename: str, result: FixResult, n: int, note: str) -> tuple[str | None, str, str]:
        parts = [f"File {filename}:\n{_numbered(original, filename)}"]
        if result.initial_report:
            frames = "\n".join(str(f) for f in result.initial_report.user_frames(filename)[:8])
            parts.append(f"Sanitizer report:\n{result.initial_report.raw}\n\nFrames in {filename}:\n{frames}")
        else:
            parts.append("The original file does not build or run cleanly:\n" + result.attempts[0].failure_summary())
        if self.config.args or self.config.stdin:
            parts.append(f"The program is run with args {self.config.args!r} and stdin {self.config.stdin!r}.")
        if result.triage:
            parts.append(f"Triage note from a fast model (may be imperfect):\n{result.triage}")
        # Show the latest failed attempts so the model does not repeat itself.
        for a in result.attempts[1:][-2:]:
            parts.append(f"Attempt {a.number} produced this file:\n```c\n{a.source}\n```\n{a.failure_summary()}")
        if note:
            parts.append(note)

        self.emit("patch_start", {"attempt": n, "model": self.config.patch_model})
        reply = self.llm.chat(
            self.config.patch_model,
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}],
            temperature=0.2,
            max_tokens=16384,
        )
        fields = dict(_FIELD.findall(reply.text))
        blocks = _CODE_BLOCK.findall(reply.text)
        code = blocks[-1].rstrip() + "\n" if blocks else None
        self.emit(
            "patch",
            {
                "attempt": n,
                "model": reply.model,
                "root_cause": fields.get("ROOT CAUSE", ""),
                "fix": fields.get("FIX", ""),
                "seconds": round(reply.seconds, 1),
                "tokens": reply.prompt_tokens + reply.completion_tokens,
            },
        )
        return code, fields.get("ROOT CAUSE", "").strip(), fields.get("FIX", "").strip()


def _numbered(source: str, filename: str) -> str:
    lines = source.splitlines()
    width = len(str(len(lines)))
    body = "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, 1))
    return f"```c\n// {filename} (line numbers added for reference)\n{body}\n```"
