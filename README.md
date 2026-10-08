# SegSense

**Agentic debugger for C memory bugs.** SegSense compiles your C program with AddressSanitizer and
UndefinedBehaviorSanitizer, runs it, catches the crash, and has **NVIDIA Nemotron on Nebius Token
Factory** write a patch. It re-compiles and re-tests every patch and loops until the program runs clean.

> Built for the **NVIDIA x Nebius Global AI Hackathon**, *Coding and Agentic Engineering* track.

```
$ segsense fix examples/heap_overflow.c
[orig] AddressSanitizer: heap-buffer-overflow  duplicate at heap_overflow.c:8
[segsense] triage: malloc(strlen(s)) leaves no room for the NUL; strcpy on line 8 writes past it.
[try 1] asking nvidia/Llama-3_1-Nemotron-Ultra-253B-v1 for a patch ...
[try 1] clean run (exit 0)

Fixed after 1 attempt(s).
-    char *copy = malloc(strlen(s));
+    char *copy = malloc(strlen(s) + 1);
```

## Why

Segfaults, use-after-free and buffer overflows are the bugs every C programmer, from students to
kernel developers, loses hours to. The sanitizers already pinpoint *where* memory went wrong.
SegSense hands that ground truth to a model that reasons about *why*, then checks the model's
answer with the same sanitizers. A patch counts as a fix only when the program actually runs
clean. The model's claim alone is not enough.

## How it works

```
             ┌─────────────────────────────────────────────────────────────┐
  prog.c ──▶ │ 1. build   gcc/clang -fsanitize=address,undefined -g -O0     │
             │ 2. run     capture ASan / UBSan / LeakSanitizer report       │
             │ 3. parse   bug kind, faulting line, alloc/free stacks        │
             │ 4. triage  Nemotron Nano: fast root-cause hint     (1 call)  │
             │ 5. patch   Nemotron Ultra: full corrected file + reasoning   │
             │ 6. verify  rebuild + rerun; failures fed back to step 5      │
             └──────────────────────────── loop until clean ───────────────┘
                                         │
                                         ▼
                         unified diff + root-cause explanation
```

- **Two NVIDIA models, each sized for its job.** A small, fast Nemotron triages the report in about
  a second. The large reasoning Nemotron writes the patch. This follows the hackathon advice: Ultra
  for serious reasoning, Nano for fast everyday calls.
- **Grounded prompts.** The model gets line-numbered source, the trimmed sanitizer report (the
  shadow-byte dump is removed) and the stack frames that point into the user's file.
- **Self-correcting.** When a patch doesn't compile, or still crashes, the compiler error or new
  report goes back to the model together with its earlier attempt.
- **No cheating.** The prompt forbids deleting the failing code. `--expect-stdout` also rejects any
  patch that changes the program's output, so a patch can't "fix" the program by gutting it.
- **Sandboxed runs.** CPU-time and file-size limits plus a timeout; the Docker image runs as a non-root user.

Bugs it handles out of the box (see [`examples/`](examples)): heap and stack buffer overflows,
off-by-one errors, use-after-free, double free, memory leaks and signed integer overflow.

## NVIDIA and Nebius usage

| What | Where |
|---|---|
| **NVIDIA Nemotron Ultra** (patch model): root-cause reasoning and code repair | `segsense/agent.py`, `SegSense._propose` |
| **NVIDIA Nemotron Nano** (triage model): fast crash summary | `segsense/agent.py`, `SegSense._triage` |
| **Nebius Token Factory**: OpenAI-compatible inference for both models | `segsense/llm.py` |
| **Nebius Serverless Endpoints** (optional): host the web demo from the `Dockerfile` | see *Deploy* |

Every AI call in SegSense goes to an NVIDIA model. The CLI warns if you configure a model that
isn't from NVIDIA.

## Setup

Requirements: Python 3.10+, and gcc or clang with the sanitizer runtime (`libasan`). Linux or
macOS. SegSense has no Python dependencies.

```bash
git clone https://github.com/Blop77/SegSense.git
cd SegSense
pip install -e .                       # or run it without installing: python -m segsense ...

export NEBIUS_API_KEY=...              # from https://tokenfactory.nebius.com
segsense models                        # lists the NVIDIA models your key can use
```

The default model IDs live in `segsense/llm.py`. Token Factory's catalogue changes, so if
`segsense models` shows different IDs, set them explicitly:

```bash
export SEGSENSE_PATCH_MODEL=nvidia/<ultra-or-super-model-id>
export SEGSENSE_TRIAGE_MODEL=nvidia/<nano-model-id>
```

## Usage

```bash
segsense fix examples/use_after_free.c                     # writes examples/use_after_free.fixed.c
segsense fix prog.c --args "input.txt 3" --stdin in.txt    # run the program with args and input
segsense fix prog.c --expect-stdout expected.txt           # the patch must keep the output identical
segsense fix prog.c --in-place --max-attempts 6
segsense fix prog.c --no-triage                            # use the patch model only
```

The exit code is `0` when the program ends up clean, so SegSense works in CI.

### Web demo

```bash
segsense serve --port 8080      # open http://localhost:8080
```

Choose an example or paste your own C code, then press **Debug it**. The agent log streams each step
(build, crash, Nemotron triage, patch, re-test) and the patch is shown as a coloured diff.

### Deploy

```bash
docker build -t segsense .
docker run -p 8080:8080 -e NEBIUS_API_KEY=$NEBIUS_API_KEY segsense
```

The same image can run on Nebius Serverless Endpoints or any container host. The demo compiles and
runs code that visitors submit, so keep it in an isolated container like this one.

## Tests

The tests run without an API key: a scripted stand-in replaces Nemotron, and the sanitizers and
compiler are real.

```bash
python -m unittest discover -s tests -t .
```

## Project layout

```
segsense/
  sanitizer.py   compile with sanitizers, run with limits, parse ASan/UBSan/LSan reports
  llm.py         Nebius Token Factory client (stdlib, retries, strips <think> blocks)
  agent.py       the triage → patch → verify loop
  cli.py         `segsense fix | models | serve`
  web.py         streaming web demo (single page, no build step)
examples/        buggy C programs for the demo
tests/           unit and end-to-end tests
```

## Limitations and next steps

- Works on one `.c` file at a time. Multi-file projects with a Makefile are next.
- Uses gcc's and clang's sanitizers, so it doesn't run on Windows/MSVC.
- Planned: Valgrind as a second opinion, and data races via ThreadSanitizer.

## License

[MIT](LICENSE)
