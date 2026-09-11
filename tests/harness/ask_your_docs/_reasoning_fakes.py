"""Named fakes for reasoning-capture tests (clean-code rule: no ad-hoc mocks).

FakeChatCompletionsEndpoint serves canned Chat Completions bodies through
``httpx.MockTransport`` — zero network. Streaming requests (``"stream": true``) get the
SSE body, the rest get the JSON body. The recorded OpenRouter bodies under
``fixtures/openrouter_reasoning/`` are RESPONSE bodies only: no request headers, no key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "openrouter_reasoning"


def recorded_body(name: str) -> bytes:
    """One recorded OpenRouter response body: ``A.json`` (plain), ``B.sse`` / ``C.sse``."""
    return (FIXTURES / name).read_bytes()


def sse_body(chunks: list[dict[str, Any]]) -> bytes:
    """Server-sent-events framing of ``chunks`` plus the ``[DONE]`` sentinel."""
    frames = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    return (frames + "data: [DONE]\n\n").encode()


def wire_reasoning(sse: bytes, field: str = "reasoning") -> str:
    """The reasoning text exactly as it crossed the wire — the test oracle, parsed by hand."""
    text = []
    for frame in sse.decode().split("\n\n"):
        payload = frame.strip().removeprefix("data:").strip()
        if not frame.strip().startswith("data:") or payload == "[DONE]":
            continue
        for choice in json.loads(payload).get("choices", []):
            text.append((choice.get("delta") or {}).get(field) or "")
    return "".join(text)


class FakeChatCompletionsEndpoint:
    """Answers every POST with ``stream_body`` or ``json_body``; counts requests."""

    def __init__(self, *, json_body: bytes = b"{}", stream_body: bytes = b"") -> None:
        self.json_body = json_body
        self.stream_body = stream_body
        self.requests: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        if body.get("stream"):
            headers = {"content-type": "text/event-stream"}
            return httpx.Response(200, headers=headers, content=self.stream_body)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=self.json_body
        )
