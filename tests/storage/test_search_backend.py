"""SearchBackend capability seam — Protocol + FilterStrategy enum."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.protocols import TextSearchable, VectorSearchable
from pydocs_mcp.storage.search_backend import (
    FilterStrategy,
    SearchBackend,
    SqliteCompositeBackend,
    build_search_backend,
)


def test_filter_strategy_values():
    assert FilterStrategy.PREFILTER_IDS == "prefilter_ids"
    assert FilterStrategy.SERVER_SIDE == "server_side"
    assert FilterStrategy.RERANK_ONLY == "rerank_only"


def test_search_backend_protocol_surface():
    # A duck-typed object with all accessors satisfies the Protocol.
    class _Stub:
        def lexical(self):
            return None

        def dense(self):
            return None

        def multi(self):
            return None

        def hybrid(self):
            return None

        def graph(self):
            return None

        def filter_strategy(self, capability):
            return FilterStrategy.RERANK_ONLY

        def write_uow_children(self):
            return ()

        def capabilities(self):
            return {}

    assert isinstance(_Stub(), SearchBackend)


def _cfg() -> AppConfig:
    return AppConfig.load()


def test_composite_backend_capabilities_default(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert be.capabilities() == {
        "lexical": True,
        "dense": True,
        "multi": False,
        "hybrid": False,
        "graph": True,
    }


def test_composite_backend_accessor_types(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert isinstance(be.lexical(), TextSearchable)
    assert isinstance(be.dense(), VectorSearchable)
    assert be.hybrid() is None
    assert be.multi() is None  # LI disabled by default
    assert be.graph() is not None


def test_composite_filter_strategy_per_capability(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert be.filter_strategy("dense") is FilterStrategy.PREFILTER_IDS
    assert be.filter_strategy("multi") is FilterStrategy.RERANK_ONLY


def test_write_uow_children_count_default(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert len(be.write_uow_children()) == 2  # SQLite + TurboQuant; no fast-plaid when LI off


def test_build_search_backend_resolves_default_kind(tmp_path: Path):
    be = build_search_backend(_cfg(), db_path=tmp_path / "x.db")
    assert isinstance(be, SqliteCompositeBackend)


def test_build_search_backend_unknown_kind_raises(tmp_path: Path):
    cfg = _cfg()
    object.__setattr__(cfg.search_backend, "kind", "nope")  # force an unregistered kind
    with pytest.raises(ValueError):
        build_search_backend(cfg, db_path=tmp_path / "x.db")
