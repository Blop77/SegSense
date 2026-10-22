"""Compare SegSense's cJSON_Minify patch with the cJSON maintainers' own fixes.

Downloads cJSON v1.7.11 (the CVE fix) and v1.7.12 from GitHub, builds each
cJSON_Minify plus SegSense's patched version with AddressSanitizer and UBSan,
and runs all three on the same inputs: every truncation point of several
JSON-with-comments seeds plus hand-picked edge cases. Each input runs in its own
process with a timeout, so hangs are caught as well as memory errors.

Usage: python3 case_studies/cjson_minify/compare.py [--cc gcc|clang]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPSTREAM = "https://raw.githubusercontent.com/DaveGamble/cJSON/{tag}/cJSON.c"
TIMEOUT = 5  # seconds per input; longer means the function never returns

SEEDS = [
    '{"a": 1} /* c */',
    '{"s": "x\\\\y"} // t\n',
    '"q\\"w"',
    '/**/{"a":[1, 2]}',
    '{"k": "v" /* x */, "z": "\\\\"}',
    "a//b\nc",
    '"\\\\\\\\"',
]
EDGE_CASES = ["/*", "/", '"', '"\\', "\\", "*/", "//", '"\\"', "/* * /", '{"a":"b\\\\', " \t\r\n"]

MAIN = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    (void)argc;
    char *buf = malloc(strlen(argv[1]) + 1);   /* exactly sized, like a real document */
    strcpy(buf, argv[1]);
    cJSON_Minify(buf);
    printf("[%s]\n", buf);
    free(buf);
    return 0;
}
"""


def inputs() -> list[str]:
    cases = [seed[:i] for seed in SEEDS for i in range(len(seed) + 1)]
    return cases + EDGE_CASES


def upstream_minify(tag: str) -> str:
    """cJSON_Minify and its helpers from an upstream release, as standalone C."""
    with urllib.request.urlopen(UPSTREAM.format(tag=tag), timeout=60) as resp:
        src = resp.read().decode()
    code = re.search(
        r"static void skip_oneline_comment.*?\nCJSON_PUBLIC\(void\) cJSON_Minify\(char \*json\)\n\{.*?\n\}\n", src, re.S
    ).group(0)
    return '#define static_strlen(s) (sizeof(s) - sizeof(""))\n' + code.replace("CJSON_PUBLIC(void) ", "void ")


def segsense_minify() -> str:
    src = (HERE / "segsense_fixed.c").read_text()
    return re.search(r"void cJSON_Minify\(char \*json\)\n\{.*?\n\}\n", src, re.S).group(0)


def build(cc: str, name: str, code: str, workdir: Path) -> Path:
    c_file, binary = workdir / f"{name}.c", workdir / name
    c_file.write_text("#include <stddef.h>\n" + code + MAIN)
    flags = ["-g", "-O0", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    subprocess.run([cc, *flags, str(c_file), "-o", str(binary)], check=True)
    return binary


def run(binary: Path, text: str) -> str:
    env = {"ASAN_OPTIONS": "detect_leaks=1", "LSAN_OPTIONS": "use_stacks=0:use_registers=0"}
    try:
        proc = subprocess.run([str(binary), text], capture_output=True, text=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return "HANG"
    if "Sanitizer" in proc.stderr or proc.returncode != 0:
        return "MEMORY ERROR"
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cc", default="gcc")
    cc = parser.parse_args().cc

    cases = inputs()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        variants = {
            "SegSense patch": build(cc, "segsense", segsense_minify(), work),
            "cJSON 1.7.11": build(cc, "v1_7_11", upstream_minify("v1.7.11"), work),
            "cJSON 1.7.12": build(cc, "v1_7_12", upstream_minify("v1.7.12"), work),
        }
        results = {name: [run(binary, text) for text in cases] for name, binary in variants.items()}

    reference = results["cJSON 1.7.12"]
    print(f"{len(cases)} inputs, compiler {cc}, AddressSanitizer + UBSan\n")
    print(f"{'version':<16} {'memory errors':>14} {'hangs':>6} {'same output as 1.7.12':>22}")
    for name, outputs in results.items():
        errors = outputs.count("MEMORY ERROR")
        hangs = outputs.count("HANG")
        same = sum(a == b for a, b in zip(outputs, reference))
        print(f"{name:<16} {errors:>14} {hangs:>6} {same:>18}/{len(cases)}")

    print("\nInputs where the SegSense patch and cJSON 1.7.12 differ:")
    for text, ours, theirs in zip(cases, results["SegSense patch"], reference):
        if ours != theirs:
            print(f"  input {text!r:24} SegSense {ours:<22} 1.7.12 {theirs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
