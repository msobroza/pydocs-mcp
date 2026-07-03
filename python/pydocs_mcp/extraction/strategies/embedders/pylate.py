"""PyLateEmbedder — wraps PyLate's ``models.ColBERT`` (Decision A).

Lazy import: ``pylate`` is the optional ``[late-interaction]`` extra. The
import happens inside :meth:`from_config` so a default install (no extra)
never pays the cost.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
from pydocs_mcp.retrieval.config import LateInteractionConfig

_INSTALL_HINT = (
    "Late-interaction retrieval requires the 'late-interaction' extra. "
    "Install with: pip install 'pydocs-mcp[late-interaction]' (pulls "
    "pylate + sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection)."
)


@dataclass
class PyLateEmbedder:
    model_name: str
    dim: int
    document_length: int
    query_length: int
    pool_factor: int
    device: str = "cpu"
    _model: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_config(cls, cfg: LateInteractionConfig) -> PyLateEmbedder:
        # Airgap (spec D5): see local_source — force HF offline before pylate
        # (sentence-transformers underneath) can attempt a Hub fallback.
        if local_model_dir(cfg.model_name) is not None:
            enable_hf_offline()
        try:
            from pylate import models  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(_INSTALL_HINT) from e
        self = cls(
            model_name=cfg.model_name,
            dim=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            pool_factor=cfg.pool_factor,
            device=cfg.device,
        )
        # ``pool_factor`` is a PyLate INDEX-time parameter
        # (``pylate.indexes.PLAID``), not a model-time one — it controls
        # token pooling on stored vectors, applied when documents are
        # added to the index. ``models.ColBERT.__init__`` does not accept
        # it (verified on pylate==1.2.0 ColBERT signature). Keep
        # ``cfg.pool_factor`` on the dataclass for future fast-plaid
        # index wiring; just don't pass it here.
        self._model = models.ColBERT(
            model_name_or_path=cfg.model_name,
            embedding_size=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            device=cfg.device,
        )
        return self

    async def embed_query(self, text: str) -> list[np.ndarray]:
        mat = await asyncio.to_thread(
            lambda: self._model.encode(
                [text],
                is_query=True,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )[0],
        )
        return [np.asarray(row, dtype=np.float32) for row in mat]

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[list[np.ndarray], ...]:
        if not texts:
            return ()
        mats = await asyncio.to_thread(
            lambda: self._model.encode(
                list(texts),
                is_query=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ),
        )
        return tuple([np.asarray(row, dtype=np.float32) for row in mat] for mat in mats)
