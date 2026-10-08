"""Find authoritative guidance for a sanitizer finding with the Tavily search API (stdlib only)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .sanitizer import SanitizerReport

TAVILY_URL = "https://api.tavily.com/search"

# Sanitizer bug kinds mapped to their MITRE CWE weakness and the SEI CERT C rule that prevents it.
_WEAKNESSES = [
    ("heap-buffer-overflow", "CWE-122", "Heap-based Buffer Overflow", "ARR30-C", "Do not form or use out-of-bounds pointers or array subscripts"),
    ("stack-buffer-overflow", "CWE-121", "Stack-based Buffer Overflow", "ARR30-C", "Do not form or use out-of-bounds pointers or array subscripts"),
    ("global-buffer-overflow", "CWE-787", "Out-of-bounds Write", "ARR30-C", "Do not form or use out-of-bounds pointers or array subscripts"),
    ("heap-use-after-free", "CWE-416", "Use After Free", "MEM30-C", "Do not access freed memory"),
    ("double-free", "CWE-415", "Double Free", "MEM30-C", "Do not access freed memory"),
    ("detected memory leaks", "CWE-401", "Missing Release of Memory after Effective Lifetime", "MEM31-C", "Free dynamically allocated memory when no longer needed"),
    ("stack-use-after-return", "CWE-562", "Return of Stack Variable Address", "DCL30-C", "Declare objects with appropriate storage durations"),
    ("stack-use-after-scope", "CWE-562", "Return of Stack Variable Address", "DCL30-C", "Declare objects with appropriate storage durations"),
    ("signed integer overflow", "CWE-190", "Integer Overflow or Wraparound", "INT32-C", "Ensure that operations on signed integers do not result in overflow"),
    ("bad-free", "CWE-590", "Free of Memory not on the Heap", "MEM34-C", "Only free memory allocated dynamically"),
    ("SEGV", "CWE-476", "NULL Pointer Dereference", "EXP34-C", "Do not dereference null pointers"),
]


@dataclass
class Weakness:
    cwe: str
    name: str
    cert_rule: str
    cert_title: str


class ReferenceLookupError(RuntimeError):
    pass


@dataclass
class Reference:
    title: str
    url: str
    snippet: str


@dataclass
class Lookup:
    query: str
    cwe: str | None
    cert_rule: str | None = None
    references: list[Reference] = field(default_factory=list)
    seconds: float = 0.0


def classify(report: SanitizerReport) -> Weakness | None:
    """The CWE weakness and CERT C rule for a sanitizer finding, if it is one we know."""
    for needle, cwe, name, rule, title in _WEAKNESSES:
        if needle.lower() in report.kind.lower():
            return Weakness(cwe, name, rule, title)
    return None


def _clean(text: str, limit: int = 400) -> str:
    """Strip the table and navigation debris search snippets carry; '' if little prose is left."""
    text = re.sub(r"Home > CWE List > [^|]*", " ", text)
    text = re.sub(r"\|+|-{3,}|#+|\[\.\.\.\]|CWE Glossary Definition", " ", text)
    text = " ".join(text.split())
    letters = sum(c.isalpha() or c.isspace() for c in text)
    return text[:limit] if text and letters / len(text) > 0.85 else ""


class Tavily:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0, max_results: int = 4):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise ReferenceLookupError("TAVILY_API_KEY is not set.")
        self.timeout = timeout
        self.max_results = max_results

    def lookup(self, report: SanitizerReport) -> Lookup:
        """Two targeted searches, run in parallel: the CWE entry and the CERT C rule for this bug."""
        start = time.monotonic()
        weakness = classify(report)
        if weakness is None:
            query = f"{report.kind} in C: why it happens and how to fix it"
            refs = self.search(query, include_domains=["cwe.mitre.org", "wiki.sei.cmu.edu"])[:2]
            return Lookup(query=query, cwe=None, references=refs, seconds=time.monotonic() - start)

        number = weakness.cwe.split("-")[1]
        cwe_query = f"{weakness.cwe} {weakness.name}"
        cert_query = f"SEI CERT C {weakness.cert_rule} {weakness.cert_title}"
        with ThreadPoolExecutor(max_workers=2) as pool:
            cwe_hits = pool.submit(self.search, cwe_query, ["cwe.mitre.org"])
            cert_hits = pool.submit(self.search, cert_query, ["wiki.sei.cmu.edu"])
            cwe_hits, cert_hits = cwe_hits.result(), cert_hits.result()

        # Keep the canonical page from each search: the CWE definition and the CERT rule itself.
        refs = [r for r in cwe_hits if f"/definitions/{number}.html" in r.url][:1]
        refs += [r for r in cert_hits if weakness.cert_rule.lower() in (r.url + r.title).lower()][:1]
        return Lookup(
            query=f"{cwe_query} | {cert_query}",
            cwe=weakness.cwe,
            cert_rule=f"{weakness.cert_rule}. {weakness.cert_title}",
            references=refs,
            seconds=time.monotonic() - start,
        )

    def search(self, query: str, include_domains: list[str] | None = None) -> list[Reference]:
        body = {"query": query, "search_depth": "basic", "max_results": self.max_results}
        if include_domains:
            body["include_domains"] = include_domains
        req = urllib.request.Request(
            TAVILY_URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise ReferenceLookupError(f"Tavily returned HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ReferenceLookupError(f"Could not reach Tavily: {getattr(exc, 'reason', exc)}") from exc
        return [
            Reference(title=r.get("title", "").strip(), url=r["url"], snippet=_clean(r.get("content", "")))
            for r in data.get("results", [])
            if str(r.get("url", "")).startswith(("https://", "http://")) and not r["url"].lower().endswith(".pdf")
        ]


def from_env() -> Tavily | None:
    """A Tavily client when TAVILY_API_KEY is set, otherwise None (references are optional)."""
    return Tavily() if os.environ.get("TAVILY_API_KEY") else None
