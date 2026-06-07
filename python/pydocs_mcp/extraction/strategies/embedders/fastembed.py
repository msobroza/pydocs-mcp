"""FastEmbedEmbedder — Embedder backed by fastembed.TextEmbedding."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from fastembed import TextEmbedding  # type: ignore[import-not-found]

from pydocs_mcp.models import Embedding


@dataclass
class FastEmbedEmbedder:
    """Embedder backed by FastEmbed (ONNX-accelerated, no API key).

    Zero-copy from FastEmbed's TextEmbedding.embed() yields straight
    through to our Embedding type — both are np.ndarray (1D, float32).
    """

    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    # Execution device — drives the onnxruntime provider list so the same
    # config can run CPU or GPU without code changes.
    device: str = "cpu"
    _model: TextEmbedding = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.device == "cuda":
            # CPU listed second as graceful fallback when the GPU runtime
            # is absent (onnxruntime warns and uses CPU rather than crashing).
            self._model = TextEmbedding(
                model_name=self.model_name,
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
        else:
            self._model = TextEmbedding(model_name=self.model_name)

    async def embed_query(self, text: str) -> Embedding:
        results = await asyncio.to_thread(
            lambda: list(self._model.embed([text])),
        )
        # FastEmbed yields np.ndarray (float32, 1D) per document.
        return np.asarray(results[0], dtype=np.float32)

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        results = await asyncio.to_thread(
            lambda: list(self._model.embed(list(texts))),
        )
        return tuple(np.asarray(v, dtype=np.float32) for v in results)


__all__ = ("FastEmbedEmbedder",)
