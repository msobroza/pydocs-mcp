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
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from openai import AsyncOpenAI, OpenAI

from pydocs_mcp.storage.protocols import ChatMessage


@dataclass(frozen=True, slots=True)
class OpenAiLlmClient:
    model_name: str
    api_key: str | None = None
    # WHY mutable single-element list inside a frozen dataclass: the SDK
    # clients are constructed lazily on first use. A plain attribute
    # would need object.__setattr__ on every call; a list lets us cache
    # without that ceremony. Type-annotated as list to suppress slots
    # complaints.
    _async_cache: list[AsyncOpenAI] = field(
        default_factory=list, init=False, repr=False, compare=False,
    )
    _sync_cache: list[OpenAI] = field(
        default_factory=list, init=False, repr=False, compare=False,
    )

    def _async_client(self) -> AsyncOpenAI:
        if not self._async_cache:
            # Lazy construction — error surfaces here, not at server boot.
            self._async_cache.append(AsyncOpenAI(api_key=self.api_key))
        return self._async_cache[0]

    def _sync_client(self) -> OpenAI:
        if not self._sync_cache:
            self._sync_cache.append(OpenAI(api_key=self.api_key))
        return self._sync_cache[0]

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = await self._async_client().chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            response_format=rf,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = self._sync_client().chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            response_format=rf,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""


__all__ = ("OpenAiLlmClient",)
