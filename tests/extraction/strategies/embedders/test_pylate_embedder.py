"""PyLateEmbedder + build_multi_vector_embedder factory (spec AC-1, AC-3)."""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import numpy as np
import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import LateInteractionConfig
from pydocs_mcp.retrieval.protocols import MultiVectorEmbedder


def _install_fake_pylate(monkeypatch):
    """Monkeypatch a fake ``pylate.models.ColBERT`` to avoid loading torch."""
    fake_pylate = types.ModuleType("pylate")
    fake_models = types.ModuleType("pylate.models")

    class _FakeColBERT:
        def __init__(
            self,
            model_name_or_path,
            embedding_size,
            document_length,
            query_length,
            device="cpu",
            **kw,
        ):
            # ``pool_factor`` is intentionally accepted only via ``**kw``: it's
            # an index-time PyLate parameter (``pylate.indexes.PLAID``), not a
            # model-time one — ``models.ColBERT.__init__`` does not accept it
            # on the installed pylate version. The dataclass field is kept on
            # ``LateInteractionConfig`` for future fast-plaid index wiring.
            self._dim = embedding_size

        def encode(self, texts, is_query, convert_to_numpy=True, normalize_embeddings=True):
            return [np.ones((3, self._dim), dtype=np.float32) / np.sqrt(self._dim) for _ in texts]

    fake_models.ColBERT = _FakeColBERT
    fake_pylate.models = fake_models
    monkeypatch.setitem(sys.modules, "pylate", fake_pylate)
    monkeypatch.setitem(sys.modules, "pylate.models", fake_models)


@pytest.mark.asyncio
async def test_embed_query_returns_multi_vector_list(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    cfg = LateInteractionConfig(enabled=True)
    emb = PyLateEmbedder.from_config(cfg)
    out = await emb.embed_query("hello")
    assert isinstance(out, list)
    assert all(isinstance(v, np.ndarray) and v.ndim == 1 for v in out)
    # L2-normalized (each token vector unit-norm).
    for v in out:
        assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


@pytest.mark.asyncio
async def test_embed_chunks_returns_tuple_of_multi_vectors(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    cfg = LateInteractionConfig(enabled=True)
    emb = PyLateEmbedder.from_config(cfg)
    out = await emb.embed_chunks(("a", "b"))
    assert isinstance(out, tuple)
    assert len(out) == 2
    for mv in out:
        assert isinstance(mv, list)
        assert all(isinstance(v, np.ndarray) for v in mv)


def test_satisfies_protocol(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    emb = PyLateEmbedder.from_config(LateInteractionConfig(enabled=True))
    assert isinstance(emb, MultiVectorEmbedder)


def test_factory_dispatch_returns_pylate(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder

    cfg = LateInteractionConfig(enabled=True)
    emb = build_multi_vector_embedder(cfg)
    assert emb is not None
    assert isinstance(emb, MultiVectorEmbedder)


def test_factory_returns_none_when_disabled() -> None:
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder

    cfg = LateInteractionConfig(enabled=False)
    assert build_multi_vector_embedder(cfg) is None


def test_unknown_provider_raises(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    cfg = LateInteractionConfig(enabled=True)
    with pytest.raises((ValueError, ValidationError)):
        from pydocs_mcp.extraction.strategies.embedders import (
            _build_multi_vector_embedder_for_provider,
        )

        _build_multi_vector_embedder_for_provider("vespa", cfg)


def test_lazy_import_raises_actionable(monkeypatch) -> None:
    """Without ``pylate``, instantiation raises the actionable ImportError."""
    monkeypatch.delitem(sys.modules, "pylate", raising=False)
    monkeypatch.delitem(sys.modules, "pylate.models", raising=False)
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pylate" or name.startswith("pylate."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder

    with pytest.raises(ImportError) as exc:
        build_multi_vector_embedder(LateInteractionConfig(enabled=True))
    assert "pydocs-mcp[late-interaction]" in str(exc.value)


# ── airgap (spec D5): local model dir forces HF offline ──


def test_from_config_local_dir_sets_offline_env(monkeypatch, tmp_path) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    # Env snapshot/restore: enable_hf_offline() writes os.environ directly,
    # and patch.dict restores even vars that were absent before the test.
    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        cfg = LateInteractionConfig(enabled=True, model_name=str(tmp_path))
        PyLateEmbedder.from_config(cfg)
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_from_config_repo_id_does_not_touch_offline_env(monkeypatch) -> None:
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    with mock.patch.dict(os.environ):
        os.environ.pop("HF_HUB_OFFLINE", None)
        cfg = LateInteractionConfig(enabled=True)  # default repo-id model_name
        PyLateEmbedder.from_config(cfg)
        assert "HF_HUB_OFFLINE" not in os.environ


def test_from_config_tilde_is_expanded_for_the_loader(tmp_path, monkeypatch) -> None:
    # ColBERT does not expanduser, so a `~/models/x` spelling must reach the
    # loader in expanded form or it would be rejected as a malformed HF repo
    # id. POSIX-only: expanduser reads HOME.
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "models" / "x").mkdir(parents=True)
    with mock.patch.dict(os.environ):
        cfg = LateInteractionConfig(enabled=True, model_name="~/models/x")
        emb = PyLateEmbedder.from_config(cfg)
    assert emb.model_name == str(tmp_path / "models" / "x")
