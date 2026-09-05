"""OpenAIEmbedder — Embedder backed by OpenAI /v1/embeddings."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from openai import AsyncOpenAI  # type: ignore[import-not-found]

from pydocs_mcp.models import Embedding


@dataclass
class OpenAIEmbedder:
    """Embedder backed by an OpenAI-compatible ``/v1/embeddings`` endpoint.

    Defaults target OpenAI proper: reads ``OPENAI_API_KEY`` and hits
    ``api.openai.com``. Point ``base_url`` + ``api_key_env`` at any
    OpenAI-shaped service — e.g. OpenRouter for Mistral codestral-embed —
    and set ``send_dimensions=False`` for endpoints that reject OpenAI's
    Matryoshka ``dimensions`` request param and serve their own default
    dimension instead.

    Example:
        >>> OpenAIEmbedder(
        ...     model_name="mistralai/codestral-embed-2505",
        ...     dim=1536,
        ...     base_url="https://openrouter.ai/api/v1",
        ...     api_key_env="OPENROUTER_API_KEY",
        ...     send_dimensions=False,
        ... )  # doctest: +SKIP
    """

    model_name: str = "text-embedding-3-small"
    dim: int = 1536
    base_url: str | None = None
    api_key_env: str | None = None
    send_dimensions: bool = True
    _client: AsyncOpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        key_var = self.api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(key_var)
        if not api_key:
            raise RuntimeError(
                f"OpenAIEmbedder requires the {key_var} environment "
                "variable. Set it before starting the server, or pick a "
                "different embedding.provider in your YAML config.",
            )
        # base_url None -> the SDK's default (api.openai.com); an override
        # points the client at any OpenAI-compatible endpoint.
        client_kwargs: dict[str, object] = {"api_key": api_key}
        if self.base_url is not None:
            client_kwargs["base_url"] = self.base_url
        self._client = AsyncOpenAI(**client_kwargs)

    def _dimensions_kwarg(self) -> dict[str, int]:
        # OpenAI's Matryoshka ``dimensions`` param is not universal — some
        # OpenAI-compatible endpoints (Mistral codestral via OpenRouter)
        # reject it and serve their own default dimension. Omit it entirely
        # when send_dimensions is False rather than passing None.
        return {"dimensions": self.dim} if self.send_dimensions else {}

    async def embed_query(self, text: str) -> Embedding:
        resp = await self._client.embeddings.create(
            model=self.model_name,
            input=text,
            **self._dimensions_kwarg(),
        )
        # Normalize to np.ndarray float32 — match FastEmbed's output type.
        return np.asarray(resp.data[0].embedding, dtype=np.float32)

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        resp = await self._client.embeddings.create(
            model=self.model_name,
            input=list(texts),
            **self._dimensions_kwarg(),
        )
        # OpenAI returns embeddings in request order — preserve via tuple comp.
        return tuple(np.asarray(item.embedding, dtype=np.float32) for item in resp.data)


__all__ = ("OpenAIEmbedder",)
