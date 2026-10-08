import sys
import tempfile
import unittest
from pathlib import Path

from segsense.sanitizer import LEAK_CHECKS, compile_source, find_compiler, parse_report, run_binary

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

EXPECTED = {
    "heap_overflow.c": ("heap-buffer-overflow", "duplicate", 8),
    "use_after_free.c": ("heap-use-after-free", "free_list", 18),
    "off_by_one.c": ("stack-buffer-overflow", "average", 7),
    "double_free.c": ("double-free", "main", 19),
    "memory_leak.c": ("detected memory leaks", "split", 8),
    "signed_overflow.c": ("signed integer overflow", "factorial", 7),
}


class SanitizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = find_compiler()

    def _run(self, path: Path):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "prog"
            build = compile_source(path, binary, self.cc)
            self.assertTrue(build.ok, build.output)
            return run_binary(binary)

    def test_examples_are_caught_at_the_right_line(self):
        for name, (kind, func, line) in EXPECTED.items():
            if name == "memory_leak.c" and not LEAK_CHECKS:
                continue  # LeakSanitizer is Linux-only
            with self.subTest(name):
                result = self._run(EXAMPLES / name)
                self.assertFalse(result.clean)
                self.assertIn(kind, result.report.kind)
                frame = result.report.user_frames(name)[0]
                self.assertEqual((frame.function, frame.line), (func, line))

    def test_clean_program_has_no_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ok.c"
            src.write_text('#include <stdio.h>\nint main(void){puts("ok");return 0;}\n')
            result = self._run(src)
        self.assertTrue(result.clean)
        self.assertEqual(result.stdout, "ok\n")

    def test_sanitizer_runtime_failure_is_not_clean(self):
        # What Apple clang's ASan does when asked for leak detection.
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake"
            script.write_text("#!/bin/sh\necho '==42==AddressSanitizer: detect_leaks is not supported on this platform.' >&2\nexit 1\n")
            script.chmod(0o755)
            result = run_binary(script)
        self.assertFalse(result.clean)
        self.assertIn("detect_leaks is not supported", result.report.summary)

    def test_report_drops_shadow_bytes(self):
        stderr = (
            "==1==ERROR: AddressSanitizer: heap-use-after-free on address 0x1\n"
            "    #0 0x55 in main /x/p.c:5\n"
            "SUMMARY: AddressSanitizer: heap-use-after-free /x/p.c:5 in main\n"
            "Shadow bytes around the buggy address:\n  0x0: fd fd\n"
        )
        report = parse_report(stderr)
        self.assertEqual(report.kind, "heap-use-after-free")
        self.assertNotIn("Shadow bytes", report.raw)
        self.assertEqual(report.location("p.c"), "main at p.c:5")


if __name__ == "__main__":
    unittest.main()
