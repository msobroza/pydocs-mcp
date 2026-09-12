"""QueryPrefixEmbedder + wrap_query_prefix — the query-side prefix decorator."""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.retrieval.caching_embedder import CachingEmbedder
from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.retrieval.protocols import Embedder
from pydocs_mcp.retrieval.query_prefix import QueryPrefixEmbedder, wrap_query_prefix
from tests._fakes import RecordingEmbedder

_PREFIX = "Instruct: find the code\nQuery:"


class _NativeRecordingEmbedder(RecordingEmbedder):
    """A provider class that applies the prefix itself (like ST)."""

    applies_query_prefix_natively = True


async def test_embed_query_prepends_prefix() -> None:
    inner = RecordingEmbedder()
    await QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX).embed_query("q")
    assert inner.query_texts == [_PREFIX + "q"]


@pytest.mark.parametrize("blank", ["", "  \n"])
async def test_blank_query_forwarded_raw(blank: str) -> None:
    # An instruction-only vector has valid-looking neighbours that mean
    # nothing — blank input keeps the inner embedder's own semantics.
    inner = RecordingEmbedder()
    await QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX).embed_query(blank)
    assert inner.query_texts == [blank]


@pytest.mark.parametrize("texts", [("doc a", "doc b"), ()])
async def test_embed_chunks_passes_texts_through(texts: tuple[str, ...]) -> None:
    inner = RecordingEmbedder()
    out = await QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX).embed_chunks(texts)
    assert inner.chunk_batches == [list(texts)]
    assert len(out) == len(texts)
    assert inner.query_texts == []


def test_wrapper_mirrors_identity_and_satisfies_protocol() -> None:
    inner = RecordingEmbedder(dim=16, model_name="qwen/qwen3-embedding-4b")
    wrapped = QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX)
    assert (wrapped.dim, wrapped.model_name) == (16, "qwen/qwen3-embedding-4b")
    assert isinstance(wrapped, Embedder)


def test_wrap_returns_same_object_when_prefix_unset() -> None:
    inner = RecordingEmbedder()
    assert wrap_query_prefix(inner, EmbeddingConfig()) is inner


def test_wrap_returns_same_object_for_native_provider() -> None:
    inner = _NativeRecordingEmbedder()
    assert wrap_query_prefix(inner, EmbeddingConfig(query_prefix=_PREFIX)) is inner


def test_wrap_wraps_non_native_provider() -> None:
    inner = RecordingEmbedder()
    wrapped = wrap_query_prefix(inner, EmbeddingConfig(query_prefix=_PREFIX))
    assert isinstance(wrapped, QueryPrefixEmbedder)
    assert wrapped.inner is inner
    assert wrapped.query_prefix == _PREFIX


@pytest.mark.parametrize(
    ("inner", "mode"),
    [(RecordingEmbedder(), "wrap"), (_NativeRecordingEmbedder(), "native")],
)
def test_enable_emits_one_json_log_without_prefix_text(
    inner: RecordingEmbedder, mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="pydocs_mcp.retrieval.query_prefix"):
        wrap_query_prefix(inner, EmbeddingConfig(query_prefix=_PREFIX))
    records = [r for r in caplog.records if r.name == "pydocs_mcp.retrieval.query_prefix"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["event"] == "query_prefix_enabled"
    assert payload["mode"] == mode
    assert payload["prefix_chars"] == len(_PREFIX)
    assert "Instruct" not in records[0].getMessage()


def test_disabled_emits_no_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="pydocs_mcp.retrieval.query_prefix"):
        wrap_query_prefix(RecordingEmbedder(), EmbeddingConfig())
    assert not [r for r in caplog.records if r.name == "pydocs_mcp.retrieval.query_prefix"]


async def test_query_prefix_normalizes_before_prepending() -> None:
    # Same normalization as CachingEmbedder so the provider text is one
    # function of the query, whatever the cache setting.
    inner = RecordingEmbedder()
    await QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX).embed_query("  q \n")
    assert inner.query_texts == [_PREFIX + "q"]


@pytest.mark.parametrize("cache_on", [True, False])
async def test_query_prefix_cache_on_off_parity(cache_on: bool) -> None:
    inner = RecordingEmbedder()
    chain: Embedder = QueryPrefixEmbedder(inner=inner, query_prefix=_PREFIX)
    if cache_on:
        chain = CachingEmbedder(inner=chain, query_identity="id", max_entries=4, ttl_seconds=0)
    await chain.embed_query("  q \n")
    assert inner.query_texts == [_PREFIX + "q"]
