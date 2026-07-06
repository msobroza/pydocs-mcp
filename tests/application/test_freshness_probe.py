"""IndexFreshnessProbe — envelope facts with a TTL cache (spec §D4)."""

import asyncio

import pytest

from pydocs_mcp.application.freshness import EnvelopeInfo, IndexFreshnessProbe
from pydocs_mcp.storage.index_metadata import IndexMetadata

SHA_A = "a" * 40
SHA_B = "b" * 40


def _meta(git_head: str, indexed_at: float = 0.0) -> IndexMetadata:
    return IndexMetadata(
        project_name="p",
        project_root="/p",
        embedding_provider="",
        embedding_model="",
        embedding_dim=-1,
        pipeline_hash="",
        indexed_at=indexed_at,
        git_head=git_head,
    )


def _probe(**kwargs) -> IndexFreshnessProbe:
    defaults = dict(
        enabled=True,
        ttl_seconds=0.0,  # no caching unless a test opts in
        read_metadata=lambda: _meta(SHA_A, indexed_at=1000.0),
        resolve_live_head=lambda: SHA_A,
        count_packages=lambda: 42,
        now=lambda: 1000.0 + 86400.0,  # exactly 1 day after indexing
    )
    defaults.update(kwargs)
    return IndexFreshnessProbe(**defaults)


def test_current_index_not_stale() -> None:
    info = asyncio.run(_probe().envelope_info())
    assert info == EnvelopeInfo(
        indexed_commit=SHA_A,
        live_commit=SHA_A,
        age_days=1,
        package_count=42,
        stale=False,
    )


def test_divergent_head_is_stale() -> None:
    info = asyncio.run(_probe(resolve_live_head=lambda: SHA_B).envelope_info())
    assert info is not None and info.stale is True
    assert (info.indexed_commit, info.live_commit) == (SHA_A, SHA_B)


def test_missing_either_head_degrades_to_age_only() -> None:
    for meta_head, live in ((SHA_A, None), ("", SHA_B), ("", None)):
        info = asyncio.run(
            _probe(
                read_metadata=lambda h=meta_head: _meta(h, indexed_at=1000.0),
                resolve_live_head=lambda live=live: live,
            ).envelope_info()
        )
        assert info is not None and info.stale is False


def test_no_metadata_row_returns_none() -> None:
    info = asyncio.run(_probe(read_metadata=lambda: None).envelope_info())
    assert info is None


def test_disabled_probe_returns_none_without_reading() -> None:
    def boom() -> IndexMetadata:
        raise AssertionError("disabled probe must not read")

    info = asyncio.run(_probe(enabled=False, read_metadata=boom).envelope_info())
    assert info is None


def test_ttl_caches_reads() -> None:
    calls = {"n": 0}

    def counting_read():
        calls["n"] += 1
        return _meta(SHA_A)

    probe = _probe(ttl_seconds=60.0, read_metadata=counting_read)

    async def twice():
        await probe.envelope_info()
        await probe.envelope_info()

    asyncio.run(twice())
    assert calls["n"] == 1
