"""Find authoritative guidance for a sanitizer finding with the Tavily search API (stdlib only)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .sanitizer import SanitizerReport

TAVILY_URL = "https://api.tavily.com/search"

# Search only sources a C programmer should trust for memory-safety guidance.
TRUSTED_DOMAINS = ["cwe.mitre.org", "wiki.sei.cmu.edu", "owasp.org", "clang.llvm.org"]

# Sanitizer bug kinds mapped to their MITRE CWE weakness, which makes for a precise search query.
_CWE = [
    ("heap-buffer-overflow", "CWE-122", "Heap-based Buffer Overflow"),
    ("stack-buffer-overflow", "CWE-121", "Stack-based Buffer Overflow"),
    ("global-buffer-overflow", "CWE-787", "Out-of-bounds Write"),
    ("heap-use-after-free", "CWE-416", "Use After Free"),
    ("double-free", "CWE-415", "Double Free"),
    ("detected memory leaks", "CWE-401", "Missing Release of Memory after Effective Lifetime"),
    ("stack-use-after-return", "CWE-562", "Return of Stack Variable Address"),
    ("stack-use-after-scope", "CWE-562", "Return of Stack Variable Address"),
    ("signed integer overflow", "CWE-190", "Integer Overflow or Wraparound"),
    ("bad-free", "CWE-590", "Free of Memory not on the Heap"),
    ("SEGV", "CWE-476", "NULL Pointer Dereference"),
]


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
    references: list[Reference] = field(default_factory=list)
    seconds: float = 0.0


def classify(report: SanitizerReport) -> tuple[str | None, str]:
    """Return (CWE id, weakness name) for a report, or (None, the raw bug kind)."""
    for needle, cwe, name in _CWE:
        if needle.lower() in report.kind.lower():
            return cwe, name
    return None, report.kind


class Tavily:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0, max_results: int = 3):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise ReferenceLookupError("TAVILY_API_KEY is not set.")
        self.timeout = timeout
        self.max_results = max_results

    def lookup(self, report: SanitizerReport) -> Lookup:
        cwe, name = classify(report)
        topic = f"{cwe} {name}" if cwe else name
        query = f"{topic} in C: why it happens and how to fix it"
        start = time.monotonic()
        refs = self.search(query, include_domains=TRUSTED_DOMAINS)
        return Lookup(query=query, cwe=cwe, references=refs, seconds=time.monotonic() - start)

    def search(self, query: str, include_domains: list[str] | None = None) -> list[Reference]:
        body = {"query": query, "search_depth": "basic", "max_results": self.max_results, "topic": "general"}
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
            Reference(title=r.get("title", "").strip(), url=r["url"], snippet=" ".join(r.get("content", "").split())[:600])
            for r in data.get("results", [])
            if str(r.get("url", "")).startswith(("https://", "http://"))
        ]


def from_env() -> Tavily | None:
    """A Tavily client when TAVILY_API_KEY is set, otherwise None (references are optional)."""
    return Tavily() if os.environ.get("TAVILY_API_KEY") else None
