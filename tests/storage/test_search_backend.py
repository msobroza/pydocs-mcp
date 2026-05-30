"""SearchBackend capability seam — Protocol + FilterStrategy enum."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig
from pydocs_mcp.storage.protocols import TextSearchable, VectorSearchable
from pydocs_mcp.storage.search_backend import (
    FilterStrategy,
    SearchBackend,
    SqliteCompositeBackend,
    build_search_backend,
)


def test_filter_strategy_values() -> None:
    assert FilterStrategy.PREFILTER_IDS == "prefilter_ids"
    assert FilterStrategy.SERVER_SIDE == "server_side"
    assert FilterStrategy.RERANK_ONLY == "rerank_only"


def test_search_backend_protocol_surface() -> None:
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


def test_composite_backend_capabilities_default(tmp_path: Path) -> None:
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert be.capabilities() == {
        "lexical": True,
        "dense": True,
        "multi": False,
        "hybrid": False,
        "graph": True,
    }


def test_composite_backend_accessor_types(tmp_path: Path) -> None:
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert isinstance(be.lexical(), TextSearchable)
    assert isinstance(be.dense(), VectorSearchable)
    assert be.hybrid() is None
    assert be.multi() is None  # LI disabled by default
    assert be.graph() is not None


def test_composite_filter_strategy_per_capability(tmp_path: Path) -> None:
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert be.filter_strategy("dense") is FilterStrategy.PREFILTER_IDS
    assert be.filter_strategy("multi") is FilterStrategy.RERANK_ONLY


def test_write_uow_children_count_default(tmp_path: Path) -> None:
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert len(be.write_uow_children()) == 2  # SQLite + TurboQuant; no fast-plaid when LI off


def test_build_search_backend_resolves_default_kind(tmp_path: Path) -> None:
    be = build_search_backend(_cfg(), db_path=tmp_path / "x.db")
    assert isinstance(be, SqliteCompositeBackend)


def test_build_search_backend_unknown_kind_raises(tmp_path: Path) -> None:
    cfg = _cfg()
    object.__setattr__(cfg.search_backend, "kind", "nope")  # force an unregistered kind
    with pytest.raises(ValueError):
        build_search_backend(cfg, db_path=tmp_path / "x.db")


def test_composite_backend_li_enabled_wires_multi(tmp_path: Path) -> None:
    # LI-on dispatch must not import fast_plaid: ``multi()`` builds the read view
    # without importing it (the import is lazy inside ``score()``), and
    # ``write_uow_children()`` returns lambda factories whose fast_plaid import
    # lives inside the lambda body — so we assert the count without calling them.
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("late_interaction:\n  enabled: true\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.late_interaction.enabled is True
    be = SqliteCompositeBackend(config=cfg, db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    from pydocs_mcp.storage.search_backend import _FastPlaidReadStore

    assert isinstance(be.multi(), _FastPlaidReadStore)
    assert be.capabilities()["multi"] is True
    assert len(be.write_uow_children()) == 3  # SQLite + TurboQuant + fast-plaid (lambdas; not called)


def test_composite_backend_late_interaction_enabled(tmp_path: Path) -> None:
    # Mirrors ``test_composite_backend_li_enabled_wires_multi`` but flips LI on by
    # overriding the frozen ``AppConfig`` field directly (the ``object.__setattr__``
    # convention used across the suite) instead of loading an overlay YAML. Same
    # wiring assertions, and still no ``[late-interaction]`` extra required:
    # ``multi()`` only builds the read view and ``write_uow_children()`` returns
    # lambda factories whose fast_plaid import is lazy, so the count is asserted
    # without invoking them.
    cfg = _cfg()
    object.__setattr__(cfg, "late_interaction", LateInteractionConfig(enabled=True))
    be = SqliteCompositeBackend(config=cfg, db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    from pydocs_mcp.storage.search_backend import _FastPlaidReadStore

    assert isinstance(be.multi(), _FastPlaidReadStore)
    assert be.capabilities()["multi"] is True
    assert len(be.write_uow_children()) == 3  # SQLite + TurboQuant + fast-plaid
