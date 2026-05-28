"""OpenAiLlmClient — LlmClient Protocol concrete using the openai SDK.

Async surface uses openai.AsyncOpenAI; sync surface uses openai.OpenAI.
Both SDK instances are constructed **lazily on first use** — NOT in
``__post_init__`` — so a deployment that never calls ``chat()`` (e.g.,
a hybrid-only pipeline with no LLM step in its YAML) doesn't need
OPENAI_API_KEY set just to construct the composition root.

OPENAI_API_KEY env var is the default credential source — set api_key
explicitly only when you need a non-default key (e.g., per-tenant).
The OpenAIError ("api_key must be set") surfaces at first ``chat()``
call, not at server startup.

Transient ``openai.RateLimitError`` is retried with exponential backoff
(3 attempts total) so a 429 spike on one call doesn't kill the whole
pipeline — persistent failures still surface on the final attempt.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from openai import AsyncOpenAI, OpenAI, RateLimitError

from pydocs_mcp.storage.protocols import ChatMessage

# Retry policy for transient RateLimitError. Three attempts with
# exponential backoff (2s, 4s) gives ~6s headroom — enough to ride
# out a 429 spike without making the user wait forever.
_RETRY_MAX = 3
_RETRY_BACKOFF = 2.0

_T = TypeVar("_T")


async def _with_retry_async(coro_factory: Callable[[], Awaitable[_T]]) -> _T:
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            return await coro_factory()
        except RateLimitError as exc:
            last_exc = exc
            if attempt + 1 == _RETRY_MAX:
                raise
            await asyncio.sleep(_RETRY_BACKOFF * (2 ** attempt))
    assert last_exc is not None  # noqa: S101 — control-flow invariant; the loop body either returns or re-raises before this point
    raise last_exc


def _with_retry_sync(call_factory: Callable[[], _T]) -> _T:
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            return call_factory()
        except RateLimitError as exc:
            last_exc = exc
            if attempt + 1 == _RETRY_MAX:
                raise
            # Sync path: blocking sleep — chat_sync() is itself blocking,
            # so this doesn't violate the async-context rule.
            time.sleep(_RETRY_BACKOFF * (2 ** attempt))
    assert last_exc is not None  # noqa: S101 — control-flow invariant; the loop body either returns or re-raises before this point
    raise last_exc


# WHY slots=True without frozen=True: the SDK clients are constructed
# lazily on first use. Frozen would force object.__setattr__ on every
# cache write; with slots=True we still get attribute-typo protection.
@dataclass(slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    _async: AsyncOpenAI | None = field(default=None, init=False, repr=False)
    _sync: OpenAI | None = field(default=None, init=False, repr=False)

    def _async_client(self) -> AsyncOpenAI:
        if self._async is None:
            # Lazy construction — error surfaces here, not at server boot.
            self._async = AsyncOpenAI(api_key=self.api_key)
        return self._async

    def _sync_client(self) -> OpenAI:
        if self._sync is None:
            self._sync = OpenAI(api_key=self.api_key)
        return self._sync

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None

        async def _go() -> str:
            rsp = await self._async_client().chat.completions.create(
                model=self.model_name,
                messages=list(messages),
                response_format=rf,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return rsp.choices[0].message.content or ""

        return await _with_retry_async(_go)

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None

        def _go() -> str:
            rsp = self._sync_client().chat.completions.create(
                model=self.model_name,
                messages=list(messages),
                response_format=rf,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return rsp.choices[0].message.content or ""

        return _with_retry_sync(_go)


__all__ = ("OpenAiLlmClient",)
