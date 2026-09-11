"""An embedder's ``model_name`` must be the spelling the config asked for.

``EmbedChunksStage`` records ``embedder.model_name`` and ``PackageBuildStage``
persists it as ``packages.embedding_model``. For a bundle with no
``index_metadata`` row — one indexed through ``ProjectIndexer`` directly, as the
benchmark harness does, or by a CLI run interrupted before the stamp —
multirepo's serve-time guard falls back to that column and compares it against
``config.embedding.model_name``. ``index_metadata`` itself always stores the
configured spelling. So the stamp and every comparison agree only if providers
report the configured spelling verbatim.

Two providers used to rewrite it: side-loading a local model directory turned
``model_name`` into the expanded path (``~/models/x`` -> ``/home/u/models/x``),
so an airgap bundle without a metadata row was rejected at serve time with an
embedder-mismatch error for a model it was, in fact, built with. The expanded
path is what the model LOADER needs; it is not the model's identity.

The model runtime is stubbed throughout: fastembed and sentence-transformers
construct their model eagerly, so building them for real would hit the network
(or silently depend on a warm local cache). Only the loader is stubbed — the
real embedder object is built and its real ``model_name`` read back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig


class _StubModel:
    """Stands in for a loaded TextEmbedding / SentenceTransformer / ColBERT."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


@pytest.mark.real_embedder
def test_fastembed_reports_the_configured_model_name(monkeypatch) -> None:
    from pydocs_mcp.extraction.strategies.embedders import fastembed as fastembed_module
    from pydocs_mcp.extraction.strategies.embedders import providers

    monkeypatch.setattr(fastembed_module, "TextEmbedding", _StubModel)

    name = "BAAI/bge-small-en-v1.5"
    cfg = EmbeddingConfig(provider="fastembed", model_name=name, dim=384)
    assert providers.embedder_registry.build("fastembed", cfg).model_name == name


@pytest.mark.real_embedder
def test_openai_reports_the_configured_model_name(monkeypatch) -> None:
    from pydocs_mcp.extraction.strategies.embedders import providers

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    name = "text-embedding-3-small"
    cfg = EmbeddingConfig(provider="openai", model_name=name, dim=1536)
    assert providers.embedder_registry.build("openai", cfg).model_name == name


@pytest.mark.real_embedder
def test_sentence_transformers_reports_the_configured_hub_id(monkeypatch) -> None:
    from pydocs_mcp.extraction.strategies.embedders import providers
    from pydocs_mcp.extraction.strategies.embedders import (
        sentence_transformers as st_module,
    )

    # Stub the loader METHOD rather than importing sentence_transformers: the
    # library import costs ~15s of torch, and a sibling test exists precisely to
    # keep that import out of the suite.
    monkeypatch.setattr(
        st_module.SentenceTransformersEmbedder,
        "_load_sentence_transformer",
        lambda self: _StubModel(),
    )
    name = "sentence-transformers/all-MiniLM-L6-v2"
    cfg = EmbeddingConfig(provider="sentence_transformers", model_name=name, dim=384)
    assert providers.embedder_registry.build("sentence_transformers", cfg).model_name == name


@pytest.mark.real_embedder
def test_sentence_transformers_keeps_the_side_loaded_spelling(monkeypatch, tmp_path: Path) -> None:
    """Airgap: the loader gets the expanded directory, the identity stays as configured."""
    from pydocs_mcp.extraction.strategies.embedders import (
        sentence_transformers as st_module,
    )

    model_dir = tmp_path / "models" / "side-loaded"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    spelling = (
        str(tmp_path / "models" / "side-loaded") + "/"
    )  # trailing slash: a spelling, not a path
    monkeypatch.setattr(st_module, "enable_hf_offline", lambda: None)

    seen: dict[str, str] = {}

    def _fake_loader(self):
        seen["load_path"] = self._load_path
        return _StubModel()

    monkeypatch.setattr(
        st_module.SentenceTransformersEmbedder, "_load_sentence_transformer", _fake_loader
    )

    embedder = st_module.SentenceTransformersEmbedder(model_name=spelling, dim=384)

    assert embedder.model_name == spelling
    assert Path(seen["load_path"]) == model_dir


@pytest.mark.real_embedder
def test_pylate_keeps_the_side_loaded_spelling(monkeypatch, tmp_path: Path) -> None:
    """Same guarantee on the late-interaction path."""
    from pydocs_mcp.extraction.strategies.embedders import pylate as pylate_module

    model_dir = tmp_path / "models" / "colbert-side-loaded"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}")
    spelling = str(tmp_path / "models" / "colbert-side-loaded") + "/"
    monkeypatch.setattr(pylate_module, "enable_hf_offline", lambda: None)

    seen: dict[str, str] = {}

    def _fake_import(model_path: str):
        seen["import_path"] = model_path
        return object()

    def _fake_build(models, model_path: str, cfg):
        seen["build_path"] = model_path
        return _StubModel()

    monkeypatch.setattr(pylate_module, "_import_pylate_models", _fake_import)
    monkeypatch.setattr(pylate_module, "_build_colbert", _fake_build)

    embedder = pylate_module.PyLateEmbedder.from_config(
        LateInteractionConfig(enabled=True, model_name=spelling)
    )

    assert embedder.model_name == spelling
    assert Path(seen["build_path"]) == model_dir


@pytest.mark.real_embedder
def test_query_cache_wrapper_preserves_the_model_name() -> None:
    from pydocs_mcp.retrieval.factories import wrap_query_cache
    from tests._fakes import MockEmbedder

    inner = MockEmbedder(dim=8, model_name="some/model-v3")
    cfg = EmbeddingConfig(model_name="some/model-v3", dim=8)
    cfg.query_cache.enabled = True

    assert wrap_query_cache(inner, cfg).model_name == "some/model-v3"
