"""Minimal client for Nebius Token Factory's OpenAI-compatible API (stdlib only)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"

# Nemotron 3 Ultra (serious reasoning) writes the patch; Nemotron 3 Nano (fast) triages the crash.
# Model IDs on Token Factory change over time: run `segsense models` to see what is live
# and override with SEGSENSE_PATCH_MODEL / SEGSENSE_TRIAGE_MODEL.
DEFAULT_PATCH_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
DEFAULT_TRIAGE_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def is_nvidia_model(model: str) -> bool:
    """Hackathon rule: SegSense's AI must be NVIDIA open models (Nemotron family)."""
    m = model.lower()
    return m.startswith("nvidia/") or "nemotron" in m


_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(RuntimeError):
    pass


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


class TokenFactory:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 180.0):
        self.api_key = api_key or os.environ.get("NEBIUS_API_KEY")
        if not self.api_key:
            raise LLMError("NEBIUS_API_KEY is not set. Create a key in Nebius Token Factory and export it.")
        self.base_url = (base_url or os.environ.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> ChatResult:
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        start = time.monotonic()
        data = self._request("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response from Token Factory: {json.dumps(data)[:500]}") from exc
        if not _THINK.sub("", text).strip() and choice.get("finish_reason") == "length":
            # Reasoning models can spend the whole budget thinking and never reach the answer.
            raise LLMError(f"{model} used all {max_tokens} tokens before answering; raise max_tokens.")
        usage = data.get("usage") or {}
        return ChatResult(
            text=_THINK.sub("", text).strip(),
            model=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            seconds=time.monotonic() - start,
        )

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        return sorted(m["id"] for m in data.get("data", []))

    def _request(self, method: str, path: str, body: dict | None = None, retries: int = 4) -> dict:
        payload = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                self.base_url + path,
                data=payload,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise LLMError(f"Token Factory returned HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise LLMError(f"Could not reach Token Factory at {self.base_url}: {exc.reason}") from exc
        raise LLMError("unreachable")
