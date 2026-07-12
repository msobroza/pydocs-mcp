"""Composition-root wiring for the query-embedding cache (AC-11/12/16).

Pins the two production effects of the sharing hoist:

- W1 regression pin (AC-12): a multi-bundle workspace constructs the
  embedder exactly ONCE — previously ``build_retrieval_context`` built a
  fresh instance per project bundle (N bundles = N model loads).
- W2 regression pin (AC-16): one unscoped ``search_codebase`` over N
  projects embeds the query text exactly ONCE — the per-project pipelines
  share one ``CachingEmbedder``, so the ``asyncio.gather`` fan-out
  coalesces/hits instead of embedding N times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Embedding
from pydocs_mcp.retrieval.caching_embedder import CachingEmbedder
from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig, QueryCacheConfig
from pydocs_mcp.retrieval.factories import (
    wrap_multi_vector_query_cache,
    wrap_query_cache,
)
from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata
from tests._fakes import MockEmbedder


def _stamp_db(path: Path, *, name: str, model: str, dim: int) -> Path:
    conn = open_index_database(path)
    write_index_metadata(
        conn,
        IndexMetadata(
            project_name=name,
            project_root=f"/src/{name}",
            embedding_provider="fastembed",
            embedding_model=model,
            embedding_dim=dim,
            pipeline_hash="h",
            indexed_at=1.0,
        ),
    )
    conn.close()
    return path


def _two_bundle_workspace(tmp_path: Path, cfg: AppConfig) -> Path:
    for name, suffix in (("frontend", "0000000000"), ("backend", "1111111111")):
        _stamp_db(
            tmp_path / f"{name}_{suffix}.db",
            name=name,
            model=cfg.embedding.model_name,
            dim=cfg.embedding.dim,
        )
    return tmp_path


class _CountingMockEmbedder:
    """Embedder spy around MockEmbedder recording every embed_query text.

    Composition, not subclassing: MockEmbedder is a slots dataclass, and
    ``@dataclass(slots=True)`` recreates the class object, which breaks
    zero-arg ``super()`` in subclass methods.
    """

    def __init__(self, dim: int, calls: list[str]) -> None:
        self._inner = MockEmbedder(dim=dim)
        self._calls = calls
        self.dim = dim
        self.model_name = self._inner.model_name

    async def embed_query(self, text: str) -> Embedding:
        self._calls.append(text)
        return await self._inner.embed_query(text)

    async def embed_chunks(self, texts) -> tuple[Embedding, ...]:
        return await self._inner.embed_chunks(texts)


# ── AC-11: wrap_query_cache respects the enabled toggle ────────────────────


def test_wrap_query_cache_disabled_returns_inner_unwrapped() -> None:
    inner = MockEmbedder()
    cfg = EmbeddingConfig(query_cache=QueryCacheConfig(enabled=False))

    assert wrap_query_cache(inner, cfg) is inner, (
        "disabled → the inner embedder itself, no wrapper in the object graph"
    )


def test_wrap_query_cache_enabled_wraps_with_config_identity() -> None:
    inner = MockEmbedder()
    cfg = EmbeddingConfig()  # query_cache.enabled defaults to True

    wrapped = wrap_query_cache(inner, cfg)

    assert isinstance(wrapped, CachingEmbedder)
    assert wrapped.inner is inner
    assert wrapped.query_identity == cfg.compute_query_identity_hash()
    assert wrapped.max_entries == cfg.query_cache.max_entries
    assert wrapped.ttl_seconds == cfg.query_cache.ttl_seconds


# ── AC-12: one embedder construction per multi-bundle server ───────────────


def test_build_routers_constructs_embedder_once_for_workspace(tmp_path: Path, monkeypatch) -> None:
    from pydocs_mcp.server import build_routers

    cfg = AppConfig.load()
    workspace = _two_bundle_workspace(tmp_path, cfg)

    instances: list[MockEmbedder] = []

    def _counting_build_embedder(embedding_cfg):
        e = MockEmbedder(dim=embedding_cfg.dim)
        instances.append(e)
        return e

    monkeypatch.setattr("pydocs_mcp.retrieval.factories.build_embedder", _counting_build_embedder)

    _tools, services = build_routers(cfg, workspace=workspace)

    assert len(services) == 2
    assert len(instances) == 1, (
        f"expected ONE embedder construction for the whole workspace, got "
        f"{len(instances)} — per-project construction is the W1 regression "
        f"(N bundles = N model loads)"
    )


# ── AC-16: cross-project fan-out embeds the query once ────────────────────


@pytest.mark.asyncio
async def test_unscoped_search_embeds_query_once_across_projects(
    tmp_path: Path, monkeypatch
) -> None:
    from pydocs_mcp.application.mcp_inputs import SearchInput
    from pydocs_mcp.server import build_routers

    cfg = AppConfig.load()
    workspace = _two_bundle_workspace(tmp_path, cfg)

    inner_calls: list[str] = []
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.factories.build_embedder",
        lambda embedding_cfg: _CountingMockEmbedder(embedding_cfg.dim, inner_calls),
    )

    tools, services = build_routers(cfg, workspace=workspace)
    assert len(services) == 2

    await tools.search_codebase(SearchInput(query="needle", kind="docs"))

    assert inner_calls == ["needle"], (
        f"expected exactly one inner embed across the {len(services)}-project "
        f"asyncio.gather fan-out, got {inner_calls!r} — N identical embeds "
        f"per search is the W2 regression"
    )


# ── Multi-vector twin wiring: wrap_multi_vector_query_cache ────────────────


class _FakeMultiVectorEmbedder:
    dim: int = 8
    model_name: str = "fake-colbert"

    async def embed_query(self, text: str):
        return []

    async def embed_chunks(self, texts):
        return tuple([] for _ in texts)


def test_wrap_multi_vector_query_cache_none_passthrough() -> None:
    # build_multi_vector_embedder returns None when the [late-interaction]
    # extra is off — wrapping must not invent an embedder.
    cfg = AppConfig.load().late_interaction
    assert wrap_multi_vector_query_cache(None, cfg) is None


def test_wrap_multi_vector_query_cache_disabled_returns_inner() -> None:
    from pydocs_mcp.retrieval.config import LateInteractionConfig

    inner = _FakeMultiVectorEmbedder()
    cfg = LateInteractionConfig(query_cache=QueryCacheConfig(enabled=False))
    assert wrap_multi_vector_query_cache(inner, cfg) is inner


def test_wrap_multi_vector_query_cache_enabled_wraps_with_li_identity() -> None:
    from pydocs_mcp.retrieval.caching_embedder import CachingMultiVectorEmbedder
    from pydocs_mcp.retrieval.config import LateInteractionConfig

    inner = _FakeMultiVectorEmbedder()
    cfg = LateInteractionConfig()  # enabled by default, LI-sized LRU

    wrapped = wrap_multi_vector_query_cache(inner, cfg)

    assert isinstance(wrapped, CachingMultiVectorEmbedder)
    assert wrapped.inner is inner
    # PyLate has no query_prompt_name, so the LI pipeline hash (which folds
    # query_length / pool_factor) IS the query identity — no derived hash.
    assert wrapped.query_identity == cfg.compute_pipeline_hash()
    assert wrapped.max_entries == cfg.query_cache.max_entries == 128


def test_build_shared_retrieval_deps_wraps_multi_vector(monkeypatch) -> None:
    from pydocs_mcp.retrieval.caching_embedder import CachingMultiVectorEmbedder
    from pydocs_mcp.retrieval.factories import build_shared_retrieval_deps

    inner = _FakeMultiVectorEmbedder()
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.factories.build_multi_vector_embedder",
        lambda cfg: inner,
    )

    _embedder, mv, _llm = build_shared_retrieval_deps(AppConfig.load())

    assert isinstance(mv, CachingMultiVectorEmbedder)
    assert mv.inner is inner
