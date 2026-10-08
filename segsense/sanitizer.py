"""Compile C programs with sanitizers, run them, and parse the crash reports."""

from __future__ import annotations

import functools
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SANITIZER_FLAGS = [
    "-g",
    "-O0",
    "-fno-omit-frame-pointer",
    "-fsanitize=address,undefined",
    # Make UBSan abort instead of printing and carrying on, so it counts as a failure.
    "-fno-sanitize-recover=undefined",
]

# LeakSanitizer only works on Linux. Apple clang's ASan aborts at startup if asked for it, and
# LSan also dies inside some containers (it needs ptrace), so the compiler probe can turn it off.
# SEGSENSE_LEAK_CHECKS=0 or 1 overrides the default.
LEAK_CHECKS = os.environ.get("SEGSENSE_LEAK_CHECKS", "1" if sys.platform.startswith("linux") else "0") == "1"

RUN_ENV = {
    "ASAN_OPTIONS": f"detect_leaks={int(LEAK_CHECKS)}:abort_on_error=0:symbolize=1:color=never",
    "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1:color=never",
    # Leaks are checked after main returns, when stack and register contents are dead. Scanning them
    # anyway lets a stale copy of a leaked pointer hide the leak (with clang + UBSan, every time).
    "LSAN_OPTIONS": "use_stacks=0:use_registers=0:color=never",
}

_ASAN_ERROR = re.compile(r"ERROR: (AddressSanitizer|LeakSanitizer): (?:attempting )?(detected memory leaks|[\w-]+)")
_UBSAN_ERROR = re.compile(r"^(?P<loc>\S+?:\d+:\d+): runtime error: (?P<msg>.+)$", re.M)
_RUNTIME_MSG = re.compile(r"^==\d+==.*Sanitizer.*$", re.M)
_SUMMARY = re.compile(r"^SUMMARY: (.+)$", re.M)
_FRAME = re.compile(r"#(\d+) 0x[0-9a-f]+ in (\S+) (\S+?):(\d+)(?::(\d+))?")


@dataclass
class Frame:
    index: int
    function: str
    file: str
    line: int

    def __str__(self) -> str:
        return f"#{self.index} {self.function} at {self.file}:{self.line}"


@dataclass
class SanitizerReport:
    tool: str  # AddressSanitizer, LeakSanitizer, UndefinedBehaviorSanitizer, Signal
    kind: str  # heap-buffer-overflow, heap-use-after-free, detected memory leaks, ...
    summary: str
    frames: list[Frame] = field(default_factory=list)
    raw: str = ""

    def user_frames(self, source_name: str) -> list[Frame]:
        """Frames that point into the program under test rather than libc or the runtime."""
        return [f for f in self.frames if Path(f.file).name == source_name]

    def location(self, source_name: str) -> str:
        """Where the bug surfaced in the user's code, e.g. 'duplicate at prog.c:8'."""
        frames = self.user_frames(source_name)
        return f"{frames[0].function} at {source_name}:{frames[0].line}" if frames else self.summary

    @property
    def headline(self) -> str:
        return f"{self.tool}: {self.kind}"


@dataclass
class BuildResult:
    ok: bool
    output: str


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    report: SanitizerReport | None

    @property
    def clean(self) -> bool:
        return self.report is None and not self.timed_out and self.exit_code >= 0


def find_compiler(preferred: str | None = None) -> str:
    """Pick the first compiler that can actually link a sanitized binary.

    Some distros ship clang without its ASan runtime, so finding it on PATH is not enough.
    """
    tried = []
    for cc in dict.fromkeys(filter(None, [preferred, os.environ.get("CC"), "clang", "gcc", "cc"])):
        if shutil.which(cc):
            tried.append(cc)
            if _can_sanitize(cc):
                return cc
    if tried:
        raise RuntimeError(f"None of {', '.join(tried)} can build with -fsanitize=address,undefined; install the ASan runtime (e.g. libasan or compiler-rt).")
    raise RuntimeError("No C compiler found; install clang or gcc.")


@functools.cache
def _can_sanitize(cc: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="segsense-probe-") as tmp:
        src = Path(tmp) / "probe.c"
        src.write_text("int main(void) { return 0; }\n")
        binary = Path(tmp) / "probe"
        try:
            if not compile_source(src, binary, cc).ok:
                return False
            # Linking is not enough: the runtime must also start cleanly with our options.
            run = run_binary(binary, timeout=30)
            if run.exit_code != 0 and LEAK_CHECKS and "LeakSanitizer has encountered a fatal error" in run.stderr:
                _set_leak_checks(False)
                run = run_binary(binary, timeout=30)
            return run.exit_code == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


def _set_leak_checks(enabled: bool) -> None:
    global LEAK_CHECKS
    LEAK_CHECKS = enabled
    RUN_ENV["ASAN_OPTIONS"] = re.sub(r"detect_leaks=\d", f"detect_leaks={int(enabled)}", RUN_ENV["ASAN_OPTIONS"])


def compile_source(src: Path, out: Path, cc: str, extra_flags: list[str] | None = None) -> BuildResult:
    cmd = [cc, *SANITIZER_FLAGS, *(extra_flags or []), str(src), "-o", str(out), "-lm"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return BuildResult(ok=proc.returncode == 0, output=(proc.stdout + proc.stderr).strip())


def _limit_resources() -> None:
    # ASan reserves huge virtual ranges, so cap CPU time and output size instead of memory.
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 << 20, 16 << 20))


def run_binary(
    binary: Path,
    args: list[str] | None = None,
    stdin: str | None = None,
    timeout: float = 10.0,
    cwd: Path | None = None,
) -> RunResult:
    env = {**os.environ, **RUN_ENV}
    try:
        proc = subprocess.run(
            [str(binary), *(args or [])],
            input=stdin or "",
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=cwd,
            preexec_fn=_limit_resources if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return RunResult(-1, out, err, True, None)

    report = parse_report(proc.stderr)
    if report is None and proc.returncode < 0:
        sig = -proc.returncode
        name = signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else f"signal {sig}"
        report = SanitizerReport("Signal", name, f"process killed by {name}", raw=proc.stderr)
    if report is None and proc.returncode != 0 and _RUNTIME_MSG.search(proc.stderr):
        # The sanitizer runtime itself complained (bad option, unsupported platform). Never call that clean.
        msg = _RUNTIME_MSG.search(proc.stderr).group(0).strip()
        report = SanitizerReport("SanitizerRuntime", "sanitizer runtime error", msg, raw=_trim(proc.stderr))
    return RunResult(proc.returncode, proc.stdout, proc.stderr, False, report)


def parse_report(stderr: str) -> SanitizerReport | None:
    """Extract the first sanitizer error from a program's stderr, or None if it ran clean."""
    m = _ASAN_ERROR.search(stderr)
    if m:
        tool, kind = m.group(1), m.group(2)
        start = stderr.rfind("\n", 0, m.start()) + 1
        body = stderr[start:]
        # The shadow-byte dump is noise for a model; cut it off.
        cut = body.find("Shadow bytes around")
        if cut != -1:
            body = body[:cut]
        summary = _SUMMARY.search(stderr)
        return SanitizerReport(
            tool=tool,
            kind=kind,
            summary=summary.group(1) if summary else f"{tool}: {kind}",
            frames=_frames(body),
            raw=_trim(body),
        )

    m = _UBSAN_ERROR.search(stderr)
    if m:
        body = stderr[m.start():]
        return SanitizerReport(
            tool="UndefinedBehaviorSanitizer",
            kind=m.group("msg").strip(),
            summary=f"{m.group('loc')}: {m.group('msg').strip()}",
            frames=_ubsan_frames(m.group("loc"), body),
            raw=_trim(body),
        )
    return None


def _frames(text: str) -> list[Frame]:
    # A report can hold several stacks (bad access, then where it was allocated and
    # freed); they are kept in order, so frames[0] is always the faulting site.
    return [Frame(int(m.group(1)), m.group(2), m.group(3), int(m.group(4))) for m in _FRAME.finditer(text)]


def _ubsan_frames(loc: str, body: str) -> list[Frame]:
    file, line, _col = loc.rsplit(":", 2)
    first = Frame(0, "?", file, int(line))
    rest = _frames(body)
    return [first, *rest] if not rest else rest


def _trim(text: str, limit: int = 6000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"
