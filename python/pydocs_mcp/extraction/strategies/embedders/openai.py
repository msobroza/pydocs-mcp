"""OpenAIEmbedder — Embedder backed by OpenAI /v1/embeddings."""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from pydocs_mcp.extraction.strategies.embedders import OptionalDepMissing
from pydocs_mcp.models import Embedding

try:
    from openai import AsyncOpenAI  # type: ignore[import-not-found]
except ImportError as exc:
    raise OptionalDepMissing(
        "openai is not installed. To use the OpenAI embedder run: "
        "pip install pydocs-mcp[openai]",
    ) from exc


@dataclass
class OpenAIEmbedder:
    """Embedder backed by OpenAI /v1/embeddings. Reads OPENAI_API_KEY."""
    model_name: str = "text-embedding-3-small"
    dim: int = 1536
    _client: AsyncOpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OpenAIEmbedder requires OPENAI_API_KEY environment "
                "variable. Set it before starting the server, or pick a "
                "different embedding.provider in your YAML config.",
            )
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed_query(self, text: str) -> Embedding:
        resp = await self._client.embeddings.create(
            model=self.model_name, input=text, dimensions=self.dim,
        )
        # Normalize to np.ndarray float32 — match FastEmbed's output type.
        return np.asarray(resp.data[0].embedding, dtype=np.float32)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        resp = await self._client.embeddings.create(
            model=self.model_name, input=list(texts), dimensions=self.dim,
        )
        # OpenAI returns embeddings in request order — preserve via tuple comp.
        return tuple(
            np.asarray(item.embedding, dtype=np.float32)
            for item in resp.data
        )


__all__ = ("OpenAIEmbedder",)
