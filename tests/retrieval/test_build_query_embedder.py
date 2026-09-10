"""build_query_embedder — the ONE query-side seam: provider -> query_prefix -> cache.

Every serving path (MCP tools, CLI queries, the benchmark systems) gets its
query embedder here, so the order and the exactly-once guarantee are pinned
in one place.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import numpy as np
import pytest

from pydocs_mcp.retrieval.caching_embedder import CachingEmbedder
from pydocs_mcp.retrieval.config import EmbeddingConfig, QueryCacheConfig
from pydocs_mcp.retrieval.factories import build_query_embedder
from pydocs_mcp.retrieval.query_prefix import QueryPrefixEmbedder
from tests._fakes import RecordingEmbedder

_PREFIX = "Instruct: find the code\nQuery:"
_ST_DIM = 8


class _NativeRecordingEmbedder(RecordingEmbedder):
    """A provider class that applies the prefix itself (like ST)."""

    applies_query_prefix_natively = True


def _install_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    # Same seam the autouse conftest fixture patches: the name
    # retrieval/factories.py re-binds from the embedders package.
    monkeypatch.setattr("pydocs_mcp.retrieval.factories.build_embedder", lambda cfg: provider)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> RecordingEmbedder:
    recording = RecordingEmbedder()
    _install_provider(monkeypatch, recording)
    return recording


def test_order_is_provider_prefix_cache(provider: RecordingEmbedder) -> None:
    chain = build_query_embedder(EmbeddingConfig(query_prefix=_PREFIX))
    assert isinstance(chain, CachingEmbedder)
    assert isinstance(chain.inner, QueryPrefixEmbedder)
    assert chain.inner.inner is provider


def test_unset_prefix_keeps_pre_feature_chain(provider: RecordingEmbedder) -> None:
    chain = build_query_embedder(EmbeddingConfig())
    assert isinstance(chain, CachingEmbedder)
    assert chain.inner is provider


def test_cache_disabled_returns_prefix_wrapper(provider: RecordingEmbedder) -> None:
    cfg = EmbeddingConfig(query_prefix=_PREFIX, query_cache=QueryCacheConfig(enabled=False))
    chain = build_query_embedder(cfg)
    assert isinstance(chain, QueryPrefixEmbedder)
    assert chain.inner is provider


def test_native_provider_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _NativeRecordingEmbedder()
    _install_provider(monkeypatch, native)
    chain = build_query_embedder(EmbeddingConfig(query_prefix=_PREFIX))
    assert isinstance(chain, CachingEmbedder)
    assert chain.inner is native


def test_cached_query_identity_includes_prefix(provider: RecordingEmbedder) -> None:
    cfg = EmbeddingConfig(query_prefix=_PREFIX)
    chain = build_query_embedder(cfg)
    assert isinstance(chain, CachingEmbedder)
    assert chain.query_identity == cfg.compute_query_identity_hash()
    assert chain.query_identity != EmbeddingConfig().compute_query_identity_hash()


async def test_provider_receives_prefixed_normalized_query_once(
    provider: RecordingEmbedder,
) -> None:
    chain = build_query_embedder(EmbeddingConfig(query_prefix=_PREFIX))
    await chain.embed_query("  q \n")
    await chain.embed_query("q")  # same normalized key -> cache hit
    assert provider.query_texts == [_PREFIX + "q"]


class _FakeSentenceTransformer:
    """Stands in for sentence_transformers.SentenceTransformer: records the
    exact encode_query kwargs so the native prompt path is observable."""

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        self.max_seq_length = 0
        self.query_calls: list[dict[str, Any]] = []

    def encode_query(self, sentences: list[str], **kwargs: Any) -> np.ndarray:
        self.query_calls.append({"sentences": sentences, **kwargs})
        return np.ones((len(sentences), _ST_DIM), dtype=np.float32)


@pytest.mark.real_embedder
async def test_st_prefix_applied_exactly_once_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    cfg = EmbeddingConfig(
        provider="sentence_transformers", model_name="m", dim=_ST_DIM, query_prefix=_PREFIX
    )
    chain = build_query_embedder(cfg)
    await chain.embed_query("q")
    assert isinstance(chain, CachingEmbedder)
    assert isinstance(chain.inner, SentenceTransformersEmbedder)  # no QueryPrefixEmbedder
    calls = chain.inner.model.query_calls
    assert len(calls) == 1
    assert calls[0]["sentences"] == ["q"]
    assert calls[0]["prompt"] == _PREFIX
