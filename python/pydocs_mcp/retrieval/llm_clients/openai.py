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
from typing import Any, Literal, TypeVar

from openai import AsyncOpenAI, OpenAI, RateLimitError

from pydocs_mcp.retrieval.protocols import ChatMessage

# Retry policy for transient RateLimitError. Three attempts with
# exponential backoff (2s, 4s) gives ~6s headroom — enough to ride
# out a 429 spike without making the user wait forever.
_RETRY_MAX = 3
_RETRY_BACKOFF = 2.0

_T = TypeVar("_T")

# Reasoning models (gpt-5+, the o-series) differ from gpt-4o-class chat models
# in two request-shape ways that 400 otherwise:
#   1. temperature — they accept ONLY the default (1); any explicit value (even
#      the 0.0 we send for determinism) returns 400 "'temperature' does not
#      support X". So we omit the kwarg and let the model default stand.
#   2. token cap — they reject the legacy ``max_tokens`` param (it must be
#      ``max_completion_tokens``); even ``max_tokens: null`` 400s. So we map the
#      cap to ``max_completion_tokens`` for them.
# Standard models keep the legacy shape (explicit temperature, ``max_tokens``).
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model_name: str) -> bool:
    """True for models that reject a custom ``temperature`` and the legacy
    ``max_tokens`` param (gpt-5+, the o-series)."""
    return (model_name or "").lower().startswith(_REASONING_MODEL_PREFIXES)


async def _with_retry_async(coro_factory: Callable[[], Awaitable[_T]]) -> _T:
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX):
        try:
            return await coro_factory()
        except RateLimitError as exc:
            last_exc = exc
            if attempt + 1 == _RETRY_MAX:
                raise
            await asyncio.sleep(_RETRY_BACKOFF * (2**attempt))
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
            time.sleep(_RETRY_BACKOFF * (2**attempt))
    assert last_exc is not None  # noqa: S101 — control-flow invariant; the loop body either returns or re-raises before this point
    raise last_exc


# WHY slots=True without frozen=True: the SDK clients are constructed
# lazily on first use. Frozen would force object.__setattr__ on every
# cache write; with slots=True we still get attribute-typo protection.
@dataclass(slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    # WHY these live here (not just read from LlmConfig at call time): the
    # client is the LlmClient Protocol boundary every caller (retrieval
    # steps, application services) goes through, so configured
    # temperature/max_tokens (LlmConfig -> build_llm_client) must be
    # reachable without every caller threading the config object around.
    # chat()/chat_sync() temperature/max_tokens params default to None
    # ("use these") so a caller only overrides for a single call.
    temperature: float = 0.0
    max_tokens: int | None = None
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
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        kwargs = self._completion_kwargs(messages, rf, temperature, max_tokens)

        async def _go() -> str:
            rsp = await self._async_client().chat.completions.create(**kwargs)
            return rsp.choices[0].message.content or ""

        return await _with_retry_async(_go)

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        kwargs = self._completion_kwargs(messages, rf, temperature, max_tokens)

        def _go() -> str:
            rsp = self._sync_client().chat.completions.create(**kwargs)
            return rsp.choices[0].message.content or ""

        return _with_retry_sync(_go)

    def _completion_kwargs(
        self,
        messages: Sequence[ChatMessage],
        response_format: dict[str, str] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Shared chat.completions kwargs for the async + sync paths.

        ``temperature`` / ``max_tokens`` of ``None`` fall back to this
        client's configured defaults (``self.temperature`` /
        ``self.max_tokens``, threaded in from ``LlmConfig`` by
        ``build_llm_client``) — an explicit per-call value overrides them.

        Reasoning models (see :func:`_is_reasoning_model`) need a different
        request shape: ``temperature`` is omitted (they reject a custom value)
        and a token cap goes to ``max_completion_tokens`` rather than the legacy
        ``max_tokens``. ``None`` caps are omitted entirely — the legacy
        ``max_tokens: null`` 400s on reasoning models and is a no-op elsewhere.
        """
        effective_temperature = self.temperature if temperature is None else temperature
        effective_max_tokens = self.max_tokens if max_tokens is None else max_tokens
        reasoning = _is_reasoning_model(self.model_name)
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": list(messages),
            "response_format": response_format,
        }
        if not reasoning:
            kwargs["temperature"] = effective_temperature
        if effective_max_tokens is not None:
            kwargs["max_completion_tokens" if reasoning else "max_tokens"] = effective_max_tokens
        return kwargs


__all__ = ("OpenAiLlmClient",)
