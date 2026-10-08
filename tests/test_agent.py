"""Agent loop tests with a scripted stand-in for Nemotron, so they run without an API key."""

import unittest
from pathlib import Path

from segsense.agent import Config, SegSense
from segsense.llm import ChatResult, is_nvidia_model
from segsense.references import Lookup, Reference

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
HEAP = (EXAMPLES / "heap_overflow.c").read_text()
HEAP_FIXED = HEAP.replace("malloc(strlen(s))", "malloc(strlen(s) + 1)")


def reply(code: str) -> str:
    return f"ROOT CAUSE: buffer misses the terminator.\nFIX: allocate strlen + 1.\n```c\n{code}```\n"


class ScriptedModel:
    def __init__(self, patch_replies, triage="Heap overflow in duplicate() at line 8."):
        self.patch_replies = list(patch_replies)
        self.triage = triage
        self.calls = []

    def chat(self, model, messages, temperature=0.2, max_tokens=4096):
        self.calls.append((model, messages))
        text = self.triage if model == "triage" else self.patch_replies.pop(0)
        return ChatResult(text=text, model=model)


def make(model, references=None, events=None, **kw):
    on_event = (lambda kind, data: events.append((kind, data))) if events is not None else None
    return SegSense(model, Config(patch_model="patch", triage_model="triage", **kw), on_event=on_event, references=references)


class FakeReferences:
    def __init__(self, fail=False):
        self.fail = fail
        self.reports = []

    def lookup(self, report):
        self.reports.append(report)
        if self.fail:
            raise RuntimeError("Tavily is down")
        return Lookup(
            query="CWE-122 Heap-based Buffer Overflow in C",
            cwe="CWE-122",
            references=[Reference("CWE-122: Heap-based Buffer Overflow", "https://cwe.mitre.org/data/definitions/122.html",
                                  "Allocate enough space for the terminating null character.")],
        )


class AgentTests(unittest.TestCase):
    def test_fixes_on_first_attempt(self):
        model = ScriptedModel([reply(HEAP_FIXED)])
        result = make(model).fix(HEAP, "heap_overflow.c")

        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)  # original run + one patch
        self.assertIn("+    char *copy = malloc(strlen(s) + 1);", result.diff)
        self.assertIn("allocate strlen + 1", result.explanation)
        self.assertEqual(result.usage["calls"], 2)
        self.assertEqual(result.usage["models"], ["patch", "triage"])

        # Triage goes to the fast model; the patch prompt carries the report and the triage note.
        self.assertEqual([c[0] for c in model.calls], ["triage", "patch"])
        prompt = model.calls[1][1][1]["content"]
        self.assertIn("heap-buffer-overflow", prompt)
        self.assertIn("Triage note", prompt)
        self.assertIn(" 8 |     strcpy(copy, s);", prompt)

    def test_retries_with_feedback_after_a_bad_patch(self):
        still_broken = HEAP.replace("malloc(strlen(s))", "malloc(strlen(s) + 0)")
        model = ScriptedModel([reply(still_broken), reply(HEAP_FIXED)])
        result = make(model).fix(HEAP, "heap_overflow.c")

        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 3)
        second_prompt = model.calls[2][1][1]["content"]
        self.assertIn("Attempt 1 produced this file", second_prompt)
        self.assertIn("It still failed", second_prompt)

    def test_compile_errors_are_fed_back(self):
        model = ScriptedModel([reply("int main(void) { return x; }\n"), reply(HEAP_FIXED)])
        result = make(model).fix(HEAP, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertIn("did not compile", model.calls[2][1][1]["content"])

    def test_echoed_line_numbers_are_stripped(self):
        numbered = "".join(f"{i:>2} | {line}\n" for i, line in enumerate(HEAP_FIXED.splitlines(), 1))
        model = ScriptedModel([reply(numbered)])
        result = make(model).fix(HEAP, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.final, HEAP_FIXED)

    def test_reply_without_code_is_retried(self):
        model = ScriptedModel(["I think it is line 8.", reply(HEAP_FIXED)])
        result = make(model).fix(HEAP, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertIn("no ```c code block", model.calls[2][1][1]["content"])

    def test_expected_output_blocks_gutting_the_program(self):
        gutted = "int main(void) { return 0; }\n"
        model = ScriptedModel([reply(gutted), reply(HEAP_FIXED)])
        result = make(model, expected_stdout="hello, SegSense\n").fix(HEAP, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertEqual(result.final, HEAP_FIXED)

    def test_gives_up_after_max_attempts(self):
        model = ScriptedModel([reply(HEAP)] * 2)
        result = make(model, max_attempts=2).fix(HEAP, "heap_overflow.c")
        self.assertFalse(result.success)
        self.assertEqual(len(result.attempts), 3)

    def test_clean_program_makes_no_model_calls(self):
        model = ScriptedModel([])
        result = make(model).fix(HEAP_FIXED, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertEqual(model.calls, [])

    def test_reference_guidance_reaches_the_patch_prompt(self):
        model, refs, events = ScriptedModel([reply(HEAP_FIXED)]), FakeReferences(), []
        result = make(model, references=refs, events=events).fix(HEAP, "heap_overflow.c")

        self.assertTrue(result.success)
        self.assertEqual(refs.reports[0].kind, "heap-buffer-overflow")
        prompt = model.calls[1][1][1]["content"]
        self.assertIn("Reference guidance for CWE-122", prompt)
        self.assertIn("terminating null character", prompt)
        shown = dict(events)["references"]
        self.assertEqual(shown["items"][0]["url"], "https://cwe.mitre.org/data/definitions/122.html")

    def test_reference_failure_never_blocks_the_fix(self):
        model, events = ScriptedModel([reply(HEAP_FIXED)]), []
        result = make(model, references=FakeReferences(fail=True), events=events).fix(HEAP, "heap_overflow.c")
        self.assertTrue(result.success)
        self.assertIn("Reference lookup failed", dict(events)["error"]["message"])
        self.assertNotIn("Reference guidance", model.calls[1][1][1]["content"])

    def test_nvidia_model_check(self):
        self.assertTrue(is_nvidia_model("nvidia/Nemotron-3-Ultra-550b-a55b"))
        self.assertTrue(is_nvidia_model("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"))
        self.assertFalse(is_nvidia_model("meta-llama/Llama-3.3-70B-Instruct"))


if __name__ == "__main__":
    unittest.main()
