# Case study: cJSON CVE-2019-11834 and CVE-2019-11835

The bundled examples were written for the demo. This case study checks SegSense on a real bug that
shipped in a widely used C library and has published CVEs.

## The bug

[cJSON](https://github.com/DaveGamble/cJSON) is a small JSON parser in C used in many embedded and
IoT projects. Up to v1.7.10, `cJSON_Minify` removed whitespace and comments by walking the string in
place, and it trusted the input to be well formed:

- **CVE-2019-11835**: an unterminated `/* ...` comment. The comment loop stops at the terminating
  `'\0'`, then `json += 2` steps past it and the outer loop reads outside the buffer.
- **CVE-2019-11834**: an unterminated string ending in a backslash, such as `"oops\`. The string loop
  copies the backslash and the byte after it, which is the `'\0'`, and keeps reading past the end.

The maintainers fixed the overflows in v1.7.11 and rewrote the function in v1.7.12.

## What we ran

[`examples/real_world_cjson_minify.c`](../../examples/real_world_cjson_minify.c) is `cJSON_Minify`
from v1.7.10, copied unchanged except for the `CJSON_PUBLIC` export macro and kept under cJSON's MIT
license. A short `main()` minifies four exactly sized heap buffers: a valid document, one with escaped
quotes and backslashes, an unterminated comment, and an unterminated string.

```bash
segsense fix examples/real_world_cjson_minify.c \
  --expect-stdout case_studies/cjson_minify/expected_stdout.txt
```

`expected_stdout.txt` is the output of the maintainers' v1.7.11 on the same four inputs, so a patch is
only accepted if it minifies the valid documents the same way the real fix does.

## What SegSense did

The full log is in [`segsense_run.log`](segsense_run.log) and the patched file is in
[`segsense_fixed.c`](segsense_fixed.c).

1. AddressSanitizer reported a heap-buffer-overflow at line 42 (`while (*json)`) in `cJSON_Minify`.
2. Tavily mapped it to CWE-122 (heap-based buffer overflow) and SEI CERT C rule ARR30-C in 2.3 s.
3. Nemotron 3 Nano triaged the report: the comment loop runs past the end of the buffer.
4. **Try 1.** Nemotron 3 Ultra fixed both the unterminated comment and the unterminated string.
   SegSense re-ran the program, and AddressSanitizer found a third read past the end at line 84:
   a string that ends in a backslash right before `'\0'`.
5. **Try 2.** With that report, Ultra stopped copying after a backslash when the next byte is `'\0'`.
   The program ran clean under ASan and UBSan, and its output matched v1.7.11.

```
Fixed after 2 attempt(s).
[segsense] 3 Nemotron call(s) on Token Factory · 22,130 tokens · 47.9s model time
```

This is the loop working as intended: the first patch looked complete and the model's own explanation
covered both CVEs, but only re-running it under the sanitizer showed it missed a case.

## Comparing with the maintainers' fixes

[`compare.py`](compare.py) downloads `cJSON_Minify` from v1.7.11 and v1.7.12, builds them and the
SegSense patch with ASan and UBSan, and runs all three on 116 inputs: every truncation of seven
JSON-with-comments seeds (each one ends at an awkward byte), plus 11 edge cases such as `/*`, `"\` and
a lone `\`. Each input runs in its own process with a 5-second timeout, so hangs count as failures too.

```bash
python3 case_studies/cjson_minify/compare.py            # gcc
python3 case_studies/cjson_minify/compare.py --cc clang
```

Results (the same with gcc 13 and clang 18):

| Version | Memory errors | Hangs | Same output as 1.7.12 |
|---|---|---|---|
| SegSense patch (on 1.7.10) | 0 | 0 | 109 / 116 |
| cJSON 1.7.11 (CVE fix) | 0 | **7** | 109 / 116 |
| cJSON 1.7.12 | 0 | 0 | 116 / 116 |

- **No memory errors and no hangs.** The SegSense patch survived every input under both sanitizers.
- **cJSON 1.7.11 hangs on 7 inputs.** Its fix stopped the overflow but loops forever on inputs with a
  stray `/` that doesn't start a comment. The cJSON changelog for 1.7.12 records the same bug:
  "Fix infinite loop in `cJSON_Minify` (potential Denial of Service)" (issue #354).
- **The 7 output differences are all a stray `/`**, for example `{"a": 1} /` or `a/`. These inputs
  aren't valid JSON. The SegSense patch keeps the `/`, as the original v1.7.10 did, while 1.7.12 drops
  it. On every other input the output is identical.

## Caveats

- 116 handcrafted inputs are evidence, not a proof. We did not fuzz the patch for hours.
- We picked a bug with a known fix so the patch could be checked against it. The maintainers'
  rewrite in 1.7.12 is cleaner than a minimal patch, which is what you'd expect.
- SegSense saw only this one function and a reproducer, not the whole library.
