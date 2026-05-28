"""build_retrieval_context wires multi_vector_embedder when enabled.

The composition root (`build_retrieval_context`) is the single place that
threads the optional late-interaction embedder into `BuildContext`. When
`late_interaction.enabled=False` (the shipped default) no embedder is
allocated — pylate/torch are never imported. When enabled, the factory
constructs the configured embedder and attaches it to the context so
downstream retrieval steps can consume it via the ambient context.
"""

from __future__ import annotations

import sys
import types

import numpy as np

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context


def test_disabled_yields_no_embedder(tmp_path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    ctx = build_retrieval_context(db, cfg)
    assert ctx.multi_vector_embedder is None


def test_enabled_yields_embedder(tmp_path, monkeypatch) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()

    # Fake pylate so we don't pull torch into the test process.
    fake_pylate = types.ModuleType("pylate")
    fake_models = types.ModuleType("pylate.models")

    class _FakeColBERT:
        def __init__(self, **kw):
            self._dim = kw["embedding_size"]

        def encode(
            self,
            texts,
            is_query,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ):
            return [np.ones((3, self._dim), dtype=np.float32) / np.sqrt(self._dim) for _ in texts]

    fake_models.ColBERT = _FakeColBERT
    fake_pylate.models = fake_models
    monkeypatch.setitem(sys.modules, "pylate", fake_pylate)
    monkeypatch.setitem(sys.modules, "pylate.models", fake_models)

    cfg = AppConfig.load()
    object.__setattr__(cfg, "late_interaction", LateInteractionConfig(enabled=True))
    ctx = build_retrieval_context(db, cfg)
    assert ctx.multi_vector_embedder is not None
