"""SearchBackend capability seam — Protocol + FilterStrategy enum."""

from __future__ import annotations

from pydocs_mcp.storage.search_backend import FilterStrategy, SearchBackend


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
