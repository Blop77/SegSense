# SegSense: hackathon submission kit

Copy-ready text for the NVIDIA x Nebius Global AI Hackathon Devpost form, plus the demo video script
and a submission checklist. Edit anything that doesn't match your team's experience.

---

## Project name

**SegSense**

## Tagline

An agentic debugger that catches C memory bugs with AddressSanitizer and fixes them with NVIDIA Nemotron.

## Track

**Coding and Agentic Engineering.** SegSense is an agent that writes, runs and tests code. It
compiles C, executes it, reads the crash, asks Nemotron for a patch, and re-runs it until the
program is clean.

## What we built

SegSense takes a C program that crashes or corrupts memory and returns a verified fix. It compiles
the program with AddressSanitizer and UndefinedBehaviorSanitizer and runs it. It then parses the
sanitizer report into the bug type, the faulting line and the allocation and free stacks.

Two NVIDIA Nemotron models on Nebius Token Factory take it from there:

1. **Nemotron 3 Nano** triages the report in 1–4 seconds: what kind of bug it is, the responsible
   line, and why it happens.
2. **Nemotron 3 Ultra** reads the line-numbered source, the trimmed report and the triage note,
   then writes a root-cause explanation and a corrected file.

SegSense recompiles and re-runs every patch under the same sanitizers. A patch is accepted only
when the program actually runs clean. When a patch fails to compile, still crashes or changes the
expected output, the evidence goes back to Nemotron and it tries again.

The result is a unified diff, a plain-English root cause, and a usage line showing how many
Nemotron calls and tokens the fix took. SegSense runs as a CLI (`segsense fix prog.c`) or as a web
demo that streams each step live.

## Why we built it

We are students who write C every day (edit this line to describe your team), and memory bugs cost
us more time than anything else.
Sanitizers already know *where* memory went wrong, but their reports are hard to read, and they
don't say *why* or how to fix it. LLMs can explain and patch code, but they can't prove their patch
works. SegSense combines the two: the sanitizer provides the ground truth, Nemotron does the
reasoning, and the loop doesn't stop until the sanitizer agrees.

## How it works

```
prog.c → build (-fsanitize=address,undefined) → run → parse report
       → Nemotron 3 Nano triage → Nemotron 3 Ultra patch
       → rebuild + rerun → clean? done : feed failure back → patch again
```

- Pure Python standard library with no dependencies. It talks to Token Factory's OpenAI-compatible API.
- The prompts are grounded: line-numbered source, a sanitizer report with the noise removed, and
  only the stack frames that point into the user's file.
- Guardrails: the prompt forbids deleting the failing code, and `--expect-stdout` rejects patches
  that change the program's output. Runs get CPU, file-size and time limits.
- Leak detection (LeakSanitizer) is enabled on Linux. On macOS, where Apple clang doesn't support
  it, SegSense turns it off automatically.

## Results

On all 8 bundled examples, every bug was fixed on the first attempt using real Nemotron on Token
Factory: heap overflow, stack overflow (off-by-one), use-after-free, use-after-free across
`realloc`, double free, memory leak, signed integer overflow, and a wrong-stride matrix transpose.
Each fix took two Nemotron calls, about 4–5k tokens, and 3–7 seconds end to end.

## How we used NVIDIA and Nebius

- **NVIDIA Nemotron 3 Ultra** (`nvidia/Nemotron-3-Ultra-550b-a55b`): root-cause reasoning and code repair.
- **NVIDIA Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`): fast first-pass triage.
- **Nebius Token Factory**: serves both models through one OpenAI-compatible endpoint.
  `segsense models` lists the NVIDIA models available to your key.
- Following the hackathon guidance, Ultra handles the hard reasoning and Nano handles the fast
  call, which keeps each fix fast and cheap.

## Challenges we ran into

- **Model IDs.** The Nemotron IDs we found in third-party listings weren't the ones live on Token
  Factory. We added `segsense models` to list the live catalogue and switched the defaults to the
  Nemotron 3 family.
- **Reasoning budgets.** Nemotron 3 models think before answering, so small `max_tokens` values
  could end with no answer at all. We raised the budgets and now report a clear error if a reply is
  cut off mid-thought.
- **Echoed line numbers.** We add line numbers to the source so the model can match the
  sanitizer's line references. On the first real runs, Ultra copied those numbers into its patch,
  and 4 of 6 first attempts failed to compile. A prompt rule and a defensive strip fixed it: every
  example now succeeds on the first attempt.
- **macOS.** Apple clang's ASan aborts at startup when leak detection is requested, and SegSense
  briefly reported that as a "clean run". It now enables leak checks only on Linux, and any
  sanitizer runtime error counts as a failure.

## What's next

- Multi-file projects built with a Makefile or CMake.
- ThreadSanitizer for data races, and Valgrind as a second opinion.
- A GitHub Action that runs SegSense on a failing CI job and opens a pull request with the patch.

## Existed before the submission period?

No. The repository held only a one-line README before the submission period. All the code,
examples, tests and the demo were built during it.

---

## Feedback on Nebius Token Factory and NVIDIA models (draft: edit to match your experience)

**What worked well**

- The OpenAI-compatible API meant zero SDK lock-in. Our client is about 100 lines of the Python
  standard library.
- Latency was excellent for a 550B model: Ultra returned full patches in 2–4 seconds, and Nano
  triage took 1–4 seconds.
- Patch quality from Nemotron 3 Ultra was high. Every fix was the idiomatic one (save `next` before
  `free`, widen the type on overflow, recompute a pointer after `realloc`), never a hack that
  hides the bug.
- Having Nano and Ultra behind one endpoint made the "fast model plus strong model" pattern trivial.

**What could be better**

- Model IDs are hard to discover from outside the console. Several third-party listings show IDs
  that aren't live. A stable alias (for example `nvidia/nemotron-ultra-latest`) or a public catalogue
  page with exact IDs would help.
- The reasoning behaviour of Nemotron 3 (thinking tokens, how to turn thinking on or off through the
  API, and how it interacts with `max_tokens`) deserves a short section in the Token Factory docs.
- An example in the docs of passing per-request reasoning settings through the OpenAI-compatible
  API would save time.

---

## Demo video script (≤ 3 minutes)

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Title card or README | "C memory bugs are the worst kind to debug. SegSense finds them with AddressSanitizer and fixes them with NVIDIA Nemotron on Nebius Token Factory." |
| 0:15–0:35 | `examples/use_after_free.c` in an editor | "This linked-list code frees each node, then reads `n->next` from freed memory. It might even print the right answer, until it doesn't." |
| 0:35–1:20 | Web demo: pick `use_after_free.c`, click **Debug it** | Narrate the log as it streams: the ASan crash at line 18, Nemotron 3 Nano's triage, Nemotron 3 Ultra writing the patch, the rebuild and the clean run. |
| 1:20–1:40 | The diff panel and the usage line | "The fix saves `next` before freeing, which is the idiomatic version. SegSense only accepts it because ASan re-ran it clean. That took two Nemotron calls and about 5,000 tokens." |
| 1:40–2:10 | Web demo: `string_builder.c` | "A harder one: a pointer that's stale after `realloc`. Nemotron moves the pointer calculation after the reallocation." |
| 2:10–2:35 | Terminal: `segsense fix examples/matrix_transpose.c --expect-stdout expected.txt` | "In the CLI, `--expect-stdout` makes sure the patch keeps the program correct, not just crash-free. It also works in CI." |
| 2:35–2:55 | README results table and architecture diagram | "Eight bug classes, all fixed on the first attempt. Nano triages, Ultra reasons and patches, and the sanitizer verifies." |
| 2:55–3:00 | Repo URL | "SegSense: open source under MIT. Link below." |

Recording tips: run `python3 -m segsense serve` locally, zoom the browser to 125%, and record the
window rather than the whole screen.

---

## Requirements check

Each hackathon rule, and where SegSense meets it.

| Requirement | Status | Where |
|---|---|---|
| Runs on Nebius Token Factory or AI Cloud | ✅ | All inference goes through Token Factory (`segsense/llm.py`) |
| Uses at least one NVIDIA open model | ✅ | Nemotron 3 Ultra and Nemotron 3 Nano |
| Fits a track | ✅ | Coding and Agentic Engineering: the agent writes, runs and tests C code |
| Working project | ✅ | All 8 examples fixed on real Nemotron; tests pass in CI |
| Project description: what, why, how | ✅ | *What we built*, *Why we built it* and *How it works* above |
| Working demo URL | ✅ | https://segsense.onrender.com (Render, from `render.yaml`) |
| Demo video, public YouTube, ≤ 3 min | ✅ | https://www.youtube.com/watch?v=BUggEDYtdGw (2:17) |
| Public repository | ✅ | github.com/Blop77/SegSense is public |
| Open source license shown at the top of the repo | ✅ | MIT, which GitHub detects |
| README with setup and run instructions | ✅ | README: *Setup*, *Usage*, *Web demo*, *Deploy* |
| README highlights NVIDIA model usage | ✅ | README: *NVIDIA and Nebius usage* |
| README says where Token Factory accelerated the work | ✅ | README: *How Token Factory accelerated SegSense* |
| README lists other Nebius tools used | ⚠️ | Serverless Endpoints is listed as "not yet deployed"; update it if you host there |
| Feedback on Token Factory and NVIDIA tools | ✅ draft | *Feedback* section above: make it your team's own words |
| Explanation if the project existed before the submission period | ✅ | *Existed before the submission period?* above |
| City, if you attended Builders & Brews | ❓ | Your choice on the form |

## Submission checklist

- [x] Repository is **public**, and the MIT license shows at the top of the repo page
- [x] `main` contains the code
- [x] Demo URL: https://segsense.onrender.com (hosted on Render with `SEGSENSE_EXAMPLES_ONLY=1`)
- [x] Demo video: https://www.youtube.com/watch?v=BUggEDYtdGw (2:17)
- [ ] Track: Coding and Agentic Engineering
- [ ] Description, NVIDIA and Nebius usage, and feedback pasted from this file
- [ ] City chosen, if you attended a Builders & Brews event
- [ ] Rotate the Token Factory key that was shared during development
