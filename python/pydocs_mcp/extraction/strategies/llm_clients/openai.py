"""OpenAiLlmClient — LlmClient Protocol concrete using the openai SDK.

Async surface uses openai.AsyncOpenAI; sync surface uses openai.OpenAI.
Both SDK instances are constructed once in __post_init__ to avoid the
cold-import cost on every call.

OPENAI_API_KEY env var is the default credential source — set api_key
explicitly only when you need a non-default key (e.g., per-tenant).
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
    _async_client: AsyncOpenAI = field(init=False, repr=False, compare=False)
    _sync_client: OpenAI = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # WHY: frozen dataclass requires object.__setattr__ to populate
        # init=False fields. The SDK clients are constructed once and
        # reused across every chat() call to avoid per-request handshake.
        object.__setattr__(self, "_async_client", AsyncOpenAI(api_key=self.api_key))
        object.__setattr__(self, "_sync_client", OpenAI(api_key=self.api_key))

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        rf = {"type": "json_object"} if response_format == "json_object" else None
        rsp = await self._async_client.chat.completions.create(
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
        rsp = self._sync_client.chat.completions.create(
            model=self.model_name,
            messages=list(messages),
            response_format=rf,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return rsp.choices[0].message.content or ""


__all__ = ("OpenAiLlmClient",)
