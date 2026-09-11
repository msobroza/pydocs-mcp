"""PyLateEmbedder + build_multi_vector_embedder factory (spec AC-1, AC-3)."""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import numpy as np
import pytest
from pydantic import ValidationError

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.retrieval.config import LateInteractionConfig
from pydocs_mcp.retrieval.protocols import MultiVectorEmbedder
from tests.extraction.strategies.embedders._failing_import import install_failing_import


def _install_fake_pylate(monkeypatch, construct_error: Exception | None = None):
    """Monkeypatch a fake ``pylate.models.ColBERT`` to avoid loading torch.

    ``construct_error`` makes the fake ColBERT constructor raise it — the
    shape of an installed-but-incompatible pylate (pylate 1.0.0 on
    sentence-transformers 5.5 raised ``'MaxSim' is not a valid
    SimilarityFunction`` here)."""
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
            if construct_error is not None:
                raise construct_error
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
    # Through the PUBLIC factory, pinning both the cfg.provider dispatch and
    # the "Unknown multi-vector embedder provider" prefix (the embedding-side
    # twin is pinned by test_build_embedder.py). model_construct bypasses the
    # Literal so the runtime path is reachable.
    _install_fake_pylate(monkeypatch)
    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder

    cfg = LateInteractionConfig.model_construct(enabled=True, provider="vespa")
    with pytest.raises(ValueError, match="Unknown multi-vector embedder provider: 'vespa'"):
        build_multi_vector_embedder(cfg)


def test_lazy_import_raises_actionable(monkeypatch) -> None:
    """Without ``pylate`` installed, instantiation raises the actionable ImportError."""
    # None in sys.modules makes the import raise exactly what an absent
    # package raises: ModuleNotFoundError(name="pylate").
    monkeypatch.setitem(sys.modules, "pylate", None)
    monkeypatch.delitem(sys.modules, "pylate.models", raising=False)

    from pydocs_mcp.extraction.strategies.embedders import build_multi_vector_embedder

    with pytest.raises(ImportError) as exc:
        build_multi_vector_embedder(LateInteractionConfig(enabled=True))
    assert "pydocs-mcp[late-interaction]" in str(exc.value)
    assert isinstance(exc.value.__cause__, ModuleNotFoundError)


# ── installed-but-failing pylate: never blame a missing extra ──
# pylate 1.0.0 on sentence-transformers 5.5 failed both ways below, and the
# old `except ImportError` told users to install an extra they already had.

_MOVED_SYMBOL = "cannot import name 'generate_model_card' from 'sentence_transformers.model_card'"


def _assert_says_extra_installed(exc: BaseException, underlying: str, model: str) -> None:
    from pydocs_mcp.extraction.strategies.embedders.pylate import _INSTALL_HINT

    msg = str(exc)
    assert "extra is installed" in msg
    assert underlying in msg
    assert repr(model) in msg
    assert _INSTALL_HINT not in msg


def test_installed_pylate_with_broken_import_says_extra_installed(monkeypatch) -> None:
    original = ImportError(_MOVED_SYMBOL)
    install_failing_import(monkeypatch, "pylate.models", original)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    with pytest.raises(ImportError) as exc:
        PyLateEmbedder.from_config(LateInteractionConfig(enabled=True, model_name="org/colbert"))
    _assert_says_extra_installed(exc.value, _MOVED_SYMBOL, "org/colbert")
    assert exc.value.__cause__ is original


def test_missing_module_inside_pylate_dependency_is_not_absent_pylate(monkeypatch) -> None:
    """A ModuleNotFoundError naming a DEPENDENCY's module means pylate is
    installed but its dependency set is broken — not that pylate is absent."""
    original = ModuleNotFoundError(
        "No module named 'sentence_transformers.model_card'",
        name="sentence_transformers.model_card",
    )
    install_failing_import(monkeypatch, "pylate.models", original)
    from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder

    with pytest.raises(ImportError) as exc:
        PyLateEmbedder.from_config(LateInteractionConfig(enabled=True, model_name="org/colbert"))
    _assert_says_extra_installed(exc.value, "sentence_transformers.model_card", "org/colbert")
    assert exc.value.__cause__ is original


def test_colbert_construction_failure_says_extra_installed(monkeypatch) -> None:
    original = ValueError("'MaxSim' is not a valid SimilarityFunction")
    _install_fake_pylate(monkeypatch, construct_error=original)
    from pydocs_mcp.extraction.strategies.embedders.pylate import (
        LateInteractionModelLoadError,
        PyLateEmbedder,
    )

    with pytest.raises(LateInteractionModelLoadError) as exc:
        PyLateEmbedder.from_config(LateInteractionConfig(enabled=True, model_name="org/colbert"))
    _assert_says_extra_installed(exc.value, str(original), "org/colbert")
    assert exc.value.__cause__ is original
    assert isinstance(exc.value, PydocsMCPError)
    assert isinstance(exc.value, RuntimeError)


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

    from pydocs_mcp.extraction.strategies.embedders import pylate as pylate_module

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "models" / "x").mkdir(parents=True)
    # Spy on whatever loader is installed (the fake pylate above included) so
    # the assertion is about what REACHED the loader, not about model_name.
    real_build = pylate_module._build_colbert
    seen: dict[str, str] = {}

    def _spy(models, model_path, cfg):
        seen["load_path"] = model_path
        return real_build(models, model_path, cfg)

    monkeypatch.setattr(pylate_module, "_build_colbert", _spy)
    with mock.patch.dict(os.environ):
        cfg = LateInteractionConfig(enabled=True, model_name="~/models/x")
        emb = PyLateEmbedder.from_config(cfg)
    assert seen["load_path"] == str(tmp_path / "models" / "x")
    # The identity stays as configured: it is what packages.embedding_model and
    # multirepo's serve-time guard compare against.
    assert emb.model_name == "~/models/x"
