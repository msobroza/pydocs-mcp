"""Every embedder must report the model name its config asked for.

This invariant became load-bearing when ``PackageBuildStage`` started stamping
``Package.embedding_model`` from the embedder's ``model_name``: the stale sweep
in ``IndexingService.invalidate_stale_embeddings`` compares that stamp against
``config.embedding.model_name``. Two sources for "the current model" agree only
because every shipped provider passes ``cfg.model_name`` through untouched.

If a provider ever normalized, prefixed, or resolved the name to something else,
the two would disagree permanently: every index pass would see a model change,
clear every package's ``content_hash``, and re-extract plus re-embed the whole
corpus forever. That failure is silent and expensive, so it is pinned here
rather than left to a provider author to remember.

The model RUNTIME is stubbed in every case — fastembed and sentence-transformers
both construct their model in ``__post_init__``, so building them for real would
hit the network (or silently depend on a warm local cache) and make these tests
neither fast nor hermetic. The stub replaces only the loader; the real embedder
dataclass is constructed and its real ``model_name`` attribute is read back.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.config import EmbeddingConfig, LateInteractionConfig


class _StubModel:
    """Stands in for a loaded TextEmbedding / SentenceTransformer."""

    def __init__(self, *args, **kwargs) -> None:
        pass


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

    # Constructed eagerly by the OpenAI client; never called in this test.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")

    name = "text-embedding-3-small"
    cfg = EmbeddingConfig(provider="openai", model_name=name, dim=1536)
    assert providers.embedder_registry.build("openai", cfg).model_name == name


@pytest.mark.real_embedder
def test_sentence_transformers_reports_the_configured_model_name(monkeypatch) -> None:
    from pydocs_mcp.extraction.strategies.embedders import providers
    from pydocs_mcp.extraction.strategies.embedders import (
        sentence_transformers as st_module,
    )

    # Stub the loader METHOD rather than importing sentence_transformers: the
    # library import costs ~15s of torch and the sibling
    # test_module_import_never_touches_heavy_stack exists precisely to keep it
    # out of the suite. The real dataclass is still built and read back.
    monkeypatch.setattr(
        st_module.SentenceTransformersEmbedder,
        "_load_sentence_transformer",
        lambda self: _StubModel(),
    )

    name = "sentence-transformers/all-MiniLM-L6-v2"
    cfg = EmbeddingConfig(provider="sentence_transformers", model_name=name, dim=384)
    assert providers.embedder_registry.build("sentence_transformers", cfg).model_name == name


@pytest.mark.real_embedder
def test_query_cache_wrapper_preserves_the_model_name() -> None:
    """The composition root wraps the embedder; the identity must survive it.

    ``build_project_indexer`` hands the wrapped instance to the ingestion
    pipeline, so a wrapper that dropped or renamed ``model_name`` would break
    the stamp just as surely as a bad provider.
    """
    from pydocs_mcp.retrieval.factories import wrap_query_cache
    from tests._fakes import MockEmbedder

    inner = MockEmbedder(dim=8, model_name="some/model-v3")
    cfg = EmbeddingConfig(model_name="some/model-v3", dim=8)
    # Force the wrapping branch on regardless of the shipped default.
    cfg.query_cache.enabled = True

    assert wrap_query_cache(inner, cfg).model_name == "some/model-v3"


@pytest.mark.real_embedder
def test_multi_vector_query_cache_wrapper_preserves_the_model_name() -> None:
    """Same guarantee on the late-interaction path, which stamps unconditionally."""
    from pydocs_mcp.retrieval.factories import wrap_multi_vector_query_cache

    class _StubMultiVectorEmbedder:
        dim = 4
        model_name = "colbert/stub-v1"

        async def embed_query(self, text: str):  # pragma: no cover - not exercised
            return []

        async def embed_chunks(self, texts):  # pragma: no cover - not exercised
            return ()

    wrapped = wrap_multi_vector_query_cache(_StubMultiVectorEmbedder(), LateInteractionConfig())

    assert wrapped is not None
    assert wrapped.model_name == "colbert/stub-v1"
