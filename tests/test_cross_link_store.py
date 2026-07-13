"""Overlay sidecar store — DDL, round-trips, atomic replace, versioning (AC1-AC3).

Real SQLite against tmp paths: the feature under test IS the SQL. The
in-memory twin is exercised through the same scenarios (it is both the EROFS
degradation mode and the application-layer fake), and the Null store proves
the disabled path is silent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import (
    CrossLinkEdge,
    LinkedBundleStamp,
    WorkspaceNodeScore,
)
from pydocs_mcp.storage.factories import build_cross_link_store, overlay_path_for
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.null_cross_link_store import NullCrossLinkStore
from pydocs_mcp.storage.protocols import CrossLinkStore
from pydocs_mcp.storage.sqlite.cross_link_store import (
    _LINKS_SCHEMA_VERSION,
    SqliteCrossLinkStore,
)


def _edge(
    *,
    from_project: str = "repoa",
    from_node_id: str = "repoa.api.handler",
    to_project: str = "repob",
    to_node_id: str = "repob.core.parse",
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> CrossLinkEdge:
    return CrossLinkEdge(
        from_project=from_project,
        from_package="__project__",
        from_node_id=from_node_id,
        to_project=to_project,
        to_node_id=to_node_id,
        to_name=to_node_id,
        kind=kind,
    )


def _stamp(project: str = "repoa") -> LinkedBundleStamp:
    return LinkedBundleStamp(
        bundle_stem=f"{project}_abc123",
        project_name=project,
        bundle_path=f"/bundles/{project}_abc123.db",
        indexed_at=1000.0,
        git_head="deadbeef",
        linked_at=2000.0,
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path) -> CrossLinkStore:
    if request.param == "sqlite":
        return SqliteCrossLinkStore(path=tmp_path / "pydocs-links.sqlite3")
    return InMemoryCrossLinkStore()


class TestEdgesRoundTrip:
    async def test_edges_into_and_from(self, store: CrossLinkStore) -> None:
        edge = _edge()
        await store.replace_edges_touching("repoa", (edge,))
        into = await store.edges_into("repob", "repob.core.parse")
        assert into == (edge,)
        outof = await store.edges_from("repoa", "repoa.api.handler")
        assert outof == (edge,)
        assert await store.edges_into("repob", "repob.other") == ()

    async def test_kinds_and_limit_filters(self, store: CrossLinkStore) -> None:
        calls = _edge(kind=ReferenceKind.CALLS)
        imports = _edge(from_node_id="repoa.api.other", kind=ReferenceKind.IMPORTS)
        await store.replace_edges_touching("repoa", (calls, imports))
        only_imports = await store.edges_into(
            "repob", "repob.core.parse", kinds=(ReferenceKind.IMPORTS,)
        )
        assert only_imports == (imports,)
        capped = await store.edges_into("repob", "repob.core.parse", limit=1)
        assert len(capped) == 1

    async def test_isinstance_protocol(self, store: CrossLinkStore) -> None:
        assert isinstance(store, CrossLinkStore)


class TestReplaceEdgesTouching:
    async def test_or_delete_then_insert_is_the_staleness_unit(self, store: CrossLinkStore) -> None:
        # AC2: every edge where A is source OR target is dropped, then the
        # new set lands; edges not touching A survive.
        a_to_b = _edge()
        b_to_c = _edge(
            from_project="repob",
            from_node_id="repob.jobs.run",
            to_project="repoc",
            to_node_id="repoc.util.go",
        )
        await store.replace_edges_touching("repoa", (a_to_b,))
        await store.replace_edges_touching("repob", (a_to_b, b_to_c))
        fresh = _edge(from_node_id="repoa.api.newcaller")
        await store.replace_edges_touching("repoa", (fresh,))
        assert await store.edges_from("repoa", "repoa.api.handler") == ()
        assert await store.edges_from("repoa", "repoa.api.newcaller") == (fresh,)
        assert await store.edges_from("repob", "repob.jobs.run") == (b_to_c,)  # untouched

    async def test_double_write_is_idempotent(self, store: CrossLinkStore) -> None:
        # §3.3 step 5: an edge touches two projects and is written in BOTH
        # batches — the second insert must not raise or duplicate.
        edge = _edge()
        await store.replace_edges_touching("repoa", (edge,))
        await store.replace_edges_touching("repob", (edge,))
        assert await store.edges_into("repob", "repob.core.parse") == (edge,)

    async def test_mid_write_failure_keeps_old_rows(self, tmp_path: Path) -> None:
        # AC2 atomicity: a poisoned batch leaves the previous rows intact.
        store = SqliteCrossLinkStore(path=tmp_path / "links.sqlite3")
        good = _edge()
        await store.replace_edges_touching("repoa", (good,))
        poisoned = (_edge(from_node_id="repoa.api.other"), "not-an-edge")
        with pytest.raises(Exception):
            await store.replace_edges_touching("repoa", poisoned)  # type: ignore[arg-type]
        assert await store.edges_from("repoa", "repoa.api.handler") == (good,)


class TestStamps:
    async def test_stamp_roundtrip_and_upsert(self, store: CrossLinkStore) -> None:
        await store.stamp_bundle(_stamp("repoa"))
        await store.stamp_bundle(_stamp("repob"))
        rebumped = LinkedBundleStamp(
            bundle_stem="repoa_abc123",
            project_name="repoa",
            bundle_path="/bundles/repoa_abc123.db",
            indexed_at=1111.0,
            git_head="cafef00d",
            linked_at=3000.0,
        )
        await store.stamp_bundle(rebumped)
        stamps = {s.bundle_stem: s for s in await store.bundle_stamps()}
        assert stamps["repoa_abc123"].indexed_at == 1111.0
        assert len(stamps) == 2


class TestWorkspaceScores:
    async def test_replace_and_read_scores(self, store: CrossLinkStore) -> None:
        rows = (
            WorkspaceNodeScore(
                project="repoa", qualified_name="repoa.api.handler", pagerank=0.4, in_degree=3
            ),
            WorkspaceNodeScore(
                project="repob", qualified_name="repob.core.parse", pagerank=None, in_degree=7
            ),
        )
        await store.replace_workspace_scores(rows)
        got = await store.workspace_scores_for(
            (("repoa", "repoa.api.handler"), ("repob", "repob.core.parse"))
        )
        assert got[("repob", "repob.core.parse")].in_degree == 7
        assert got[("repob", "repob.core.parse")].pagerank is None
        # whole-table swap
        await store.replace_workspace_scores(())
        assert await store.workspace_scores_for((("repoa", "repoa.api.handler"),)) == {}


class TestSchemaVersioning:
    async def test_overlay_created_with_user_version(self, tmp_path: Path) -> None:
        path = tmp_path / "pydocs-links.sqlite3"
        store = SqliteCrossLinkStore(path=path)
        await store.bundle_stamps()  # force creation
        with sqlite3.connect(path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _LINKS_SCHEMA_VERSION

    async def test_version_mismatch_drops_and_recreates(self, tmp_path: Path) -> None:
        # AC3: relink-not-migrate — nothing of value is lost, bundles untouched.
        path = tmp_path / "pydocs-links.sqlite3"
        store = SqliteCrossLinkStore(path=path)
        await store.replace_edges_touching("repoa", (_edge(),))
        with sqlite3.connect(path) as conn:
            conn.execute(f"PRAGMA user_version = {_LINKS_SCHEMA_VERSION + 7}")
        reopened = SqliteCrossLinkStore(path=path)
        assert await reopened.edges_from("repoa", "repoa.api.handler") == ()
        with sqlite3.connect(path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == _LINKS_SCHEMA_VERSION


class TestNullStore:
    async def test_reads_empty_writes_silent(self) -> None:
        null = NullCrossLinkStore()
        assert isinstance(null, CrossLinkStore)
        await null.replace_edges_touching("repoa", (_edge(),))
        await null.stamp_bundle(_stamp())
        await null.replace_workspace_scores(())
        assert await null.edges_into("repob", "x") == ()
        assert await null.edges_from("repoa", "x") == ()
        assert await null.bundle_stamps() == ()
        assert await null.workspace_scores_for((("repoa", "x"),)) == {}


class TestPlacement:
    def test_overlay_path_workspace_local(self, tmp_path: Path) -> None:
        assert overlay_path_for(tmp_path, ()) == tmp_path / "pydocs-links.sqlite3"

    def test_overlay_path_home_fallback_for_explicit_dbs(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.db", tmp_path / "b.db"
        path = overlay_path_for(None, (b, a))
        assert path.name.endswith(".sqlite3") and "links" in str(path)
        assert path == overlay_path_for(None, (a, b))  # order-independent key

    def test_discover_workspace_never_loads_the_overlay(self, tmp_path: Path) -> None:
        # AC21 half: defense in depth — even a decoy named pydocs-links.db
        # must never be loaded as a project bundle.
        from pydocs_mcp.multirepo import discover_workspace

        (tmp_path / "pydocs-links.sqlite3").write_bytes(b"")
        (tmp_path / "pydocs-links.db").write_bytes(b"")
        with pytest.raises(ValueError, match="no .db bundles"):
            discover_workspace(tmp_path)

    def test_build_cross_link_store_factory(self, tmp_path: Path) -> None:
        store = build_cross_link_store(tmp_path / "pydocs-links.sqlite3")
        assert isinstance(store, SqliteCrossLinkStore)
