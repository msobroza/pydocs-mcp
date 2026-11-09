"""Named fakes for reasoning-capture tests (clean-code rule: no ad-hoc mocks).

FakeChatCompletionsEndpoint serves canned Chat Completions bodies through
``httpx.MockTransport`` — zero network. Streaming requests (``"stream": true``) get the
SSE body, the rest get the JSON body. The recorded OpenRouter bodies under
``fixtures/openrouter_reasoning/`` are RESPONSE bodies only: no request headers, no key.

FakeStallingStreamEndpoint and ``chat_model_kwargs_built_with_timer_field`` are the
stream-chunk timer's seams: a stream that goes silent after its first frame, and a kwargs
spy whose class declares ``stream_chunk_timeout`` (AC-19's own spy declares no field).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

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


def _completion_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    choice = {"index": 0, "delta": delta, "finish_reason": finish_reason}
    return {
        "id": "g",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [choice],
    }


class FakeStallingStreamEndpoint:
    """A streamed reply that sends its first frame, then goes silent for ``stall_seconds``.

    Async clients only: the handler is a coroutine, as ``httpx.AsyncClient`` needs to
    stream lazily. ``httpx.MockTransport`` enforces no httpx timeout, so the only limit
    that can end the silence is ``ChatOpenAI``'s stream-chunk timer. Keep the stall to a
    few seconds: a test that waits on it must never come near the upstream 120 s.
    """

    def __init__(self, *, stall_seconds: float) -> None:
        self.stall_seconds = stall_seconds
        self.requests: list[dict[str, Any]] = []
        self.transport = httpx.MockTransport(self._handle)

    async def _frames(self) -> AsyncIterator[bytes]:
        first = _completion_chunk({"role": "assistant", "content": "a"})
        yield f"data: {json.dumps(first)}\n\n".encode()
        await asyncio.sleep(self.stall_seconds)
        yield sse_body([_completion_chunk({"content": "b"}, "stop")])

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content or b"{}"))
        headers = {"content-type": "text/event-stream"}
        return httpx.Response(200, headers=headers, content=self._frames())


def chat_model_kwargs_built_with_timer_field(
    monkeypatch: pytest.MonkeyPatch, connection: Any, **factory_kwargs: Any
) -> dict[str, Any]:
    """The kwargs ``build_chat_model`` hands a class that DECLARES ``stream_chunk_timeout``.

    AC-19's spy (``chat_model_kwargs_built``) stands in for a release without the field;
    this one stands in for 1.2+, where the field is a declared model field, so the factory's
    field check sends it. It keeps the kwargs and builds nothing, like its sibling.
    """
    import langchain_openai  # the harness extra: lazy, so core tests still import this module

    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NO_BEARER
    from pydocs_mcp.harness.ask_your_docs.llm_connection import build_chat_model

    constructed: list[dict[str, Any]] = []

    class ChatOpenAITimerFieldKwargsSpy:
        model_fields: ClassVar[Mapping[str, Any]] = {"stream_chunk_timeout": None}

        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", ChatOpenAITimerFieldKwargsSpy)
    build_chat_model(connection, NO_BEARER, **factory_kwargs)
    return constructed[0]
