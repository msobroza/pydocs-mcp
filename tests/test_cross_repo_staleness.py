"""Staleness detection, incremental repair, departed purge, scores (AC18-19, 24)."""

from __future__ import annotations

import pytest

from pydocs_mcp.application.workspace_linker import (
    BundleHandle,
    WorkspaceLinker,
    detect_stale,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.node_reference import NodeReference

from ._fakes import (
    InMemoryDocumentTreeStore,
    InMemoryPackageStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

_KINDS = (ReferenceKind.CALLS, ReferenceKind.IMPORTS, ReferenceKind.INHERITS)


def _node(qname: str, *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=5,
        text="x",
        content_hash=f"h-{qname}",
        children=tuple(children),
    )


def _bundle(
    project: str,
    *,
    exports: tuple[str, ...] = (),
    refs: tuple[tuple[str, str], ...] = (),
    resolved: tuple[tuple[str, str], ...] = (),
    indexed_at: float = 1000.0,
    git_head: str | None = "head",
) -> BundleHandle:
    packages = InMemoryPackageStore()
    packages.items["__project__"] = Package(
        name="__project__",
        version="1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash=f"c-{project}",
        origin=PackageOrigin.PROJECT,
    )
    trees = InMemoryDocumentTreeStore()
    trees.by_package["__project__"] = [_node(project, *(_node(q) for q in exports))]
    ref_store = InMemoryReferenceStore()
    for from_id, to_name in refs:
        ref_store.by_package.setdefault("__project__", []).append(
            NodeReference(
                from_package="__project__",
                from_node_id=from_id,
                to_name=to_name,
                to_node_id=None,
                kind=ReferenceKind.CALLS,
            )
        )
    for from_id, to_id in resolved:
        ref_store.by_package.setdefault("__project__", []).append(
            NodeReference(
                from_package="__project__",
                from_node_id=from_id,
                to_name=to_id,
                to_node_id=to_id,
                kind=ReferenceKind.CALLS,
            )
        )
    return BundleHandle(
        project=project,
        bundle_stem=f"{project}_stem",
        bundle_path=f"/b/{project}.db",
        indexed_at=indexed_at,
        git_head=git_head,
        uow_factory=make_fake_uow_factory(packages=packages, trees=trees, references=ref_store),
    )


def _linker(*bundles: BundleHandle, workspace_scores: bool = False):
    store = InMemoryCrossLinkStore()
    return (
        WorkspaceLinker(
            bundles=bundles,
            cross_links=store,
            kinds=_KINDS,
            match_scope="project_only",
            alias_resolution="imports_graph",
            workspace_scores=workspace_scores,
        ),
        store,
    )


async def test_detect_stale_on_indexed_at_and_git_head(request) -> None:
    repoa = _bundle("repoa", indexed_at=100.0)
    repob = _bundle("repob", indexed_at=200.0)
    linker, store = _linker(repoa, repob)
    await linker.link()
    stamps = await store.bundle_stamps()
    assert detect_stale((repoa, repob), stamps) == frozenset()
    # AC18: bump repoa's indexed_at → only repoa is stale.
    bumped = _bundle("repoa", indexed_at=111.0)
    assert detect_stale((bumped, repob), stamps) == frozenset({"repoa"})
    # git_head change alone also marks stale.
    moved = _bundle("repob", indexed_at=200.0, git_head="other")
    assert detect_stale((repoa, moved), stamps) == frozenset({"repob"})
    # Missing stamp (never linked) → stale.
    assert detect_stale((_bundle("repoc"),), stamps) == frozenset({"repoc"})


async def test_incremental_relink_refreshes_only_stale_touching_edges() -> None:
    # AC18: after "reindexing" A, relink(stale={A}) refreshes A-touching
    # edges; a B↔C edge survives untouched.
    repoa = _bundle("repoa", refs=(("repoa.x", "repob.fn"),))
    repob = _bundle("repob", exports=("repob.fn",))
    repoc = _bundle("repoc", refs=(("repoc.y", "repob.fn"),))
    linker, store = _linker(repoa, repob, repoc)
    await linker.link()
    before = await store.edges_from("repoc", "repoc.y")
    await linker.link(stale_projects=frozenset({"repoa"}))
    assert await store.edges_from("repoc", "repoc.y") == before  # untouched
    assert len(await store.edges_from("repoa", "repoa.x")) == 1  # refreshed


async def test_departed_bundle_edges_and_stamp_purged() -> None:
    # AC19: a bundle removed from the workspace loses its edges + stamp.
    repoa = _bundle("repoa", refs=(("repoa.x", "repob.fn"),))
    repob = _bundle("repob", exports=("repob.fn",))
    linker, store = _linker(repoa, repob)
    await linker.link()
    assert len(await store.bundle_stamps()) == 2
    shrunk, _ = _linker(repob)
    shrunk = WorkspaceLinker(
        bundles=(repob,),
        cross_links=store,
        kinds=_KINDS,
        match_scope="project_only",
        alias_resolution="imports_graph",
        workspace_scores=False,
    )
    await shrunk.link()
    stems = {s.bundle_stem for s in await store.bundle_stamps()}
    assert stems == {"repob_stem"}
    assert await store.edges_from("repoa", "repoa.x") == ()


class TestWorkspaceScores:
    async def test_scores_computed_with_composite_identity(self) -> None:
        # AC24: one row per union-graph node keyed (project, qname);
        # in_degree ALWAYS finite; same-qname exports get distinct rows.
        repoa = _bundle("repoa", exports=("shared.fn",), refs=(("repoa.x", "repob.fn"),))
        repob = _bundle(
            "repob", exports=("repob.fn", "shared.fn"), resolved=(("repob.a", "repob.fn"),)
        )
        linker, store = _linker(repoa, repob, workspace_scores=True)
        report = await linker.link()
        assert report.workspace_scores_computed
        scores = await store.workspace_scores_for(
            (
                ("repoa", "shared.fn"),
                ("repob", "shared.fn"),
                ("repob", "repob.fn"),
            )
        )
        assert ("repoa", "shared.fn") in scores and ("repob", "shared.fn") in scores
        # repob.fn is called by the cross edge AND the local resolved edge.
        assert scores[("repob", "repob.fn")].in_degree == 2
        # pagerank: finite when [graph] installed, None otherwise — both legal.
        pagerank = scores[("repob", "repob.fn")].pagerank
        assert pagerank is None or pagerank > 0.0

    async def test_scores_disabled_drops_the_table(self) -> None:
        repoa = _bundle("repoa", refs=(("repoa.x", "repob.fn"),))
        repob = _bundle("repob", exports=("repob.fn",))
        linker, store = _linker(repoa, repob, workspace_scores=True)
        await linker.link()
        off = WorkspaceLinker(
            bundles=(repoa, repob),
            cross_links=store,
            kinds=_KINDS,
            match_scope="project_only",
            alias_resolution="imports_graph",
            workspace_scores=False,
        )
        report = await off.link()
        assert not report.workspace_scores_computed
        assert await store.workspace_scores_for((("repob", "repob.fn"),)) == {}

    async def test_pagerank_finite_with_graph_extra(self) -> None:
        # AC24 (positive clause): when the [graph] pagerank path is available
        # (networkx + scipy), pagerank is a finite float on nodes with fan-in
        # — not merely 'None-or-positive'. Skips where the extra is absent.
        from pydocs_mcp.application.workspace_linker import _try_pagerank

        if not _try_pagerank([("a", "b")])[1]:
            pytest.skip("[graph] pagerank path unavailable (needs networkx + scipy)")
        repoa = _bundle("repoa", refs=(("repoa.x", "repob.fn"),))
        repob = _bundle("repob", exports=("repob.fn",), resolved=(("repob.a", "repob.fn"),))
        linker, store = _linker(repoa, repob, workspace_scores=True)
        report = await linker.link()
        assert report.workspace_scores_computed and report.pagerank_available
        scores = await store.workspace_scores_for((("repob", "repob.fn"),))
        pagerank = scores[("repob", "repob.fn")].pagerank
        assert isinstance(pagerank, float) and pagerank > 0.0

    async def test_pagerank_degrades_without_graph_extra(self, monkeypatch) -> None:
        # AC24: [graph] absent → in_degree rows still land, pagerank NULL,
        # pagerank_available False — never a raise.
        import pydocs_mcp.application.workspace_linker as wl

        # Simulate the [graph] extra being absent: the shim returns the
        # degraded (no-pagerank) tier exactly as the ImportError path does.
        monkeypatch.setattr(wl, "_try_pagerank", lambda edges: ({}, False))
        repoa = _bundle("repoa", refs=(("repoa.x", "repob.fn"),))
        repob = _bundle("repob", exports=("repob.fn",))
        linker, store = _linker(repoa, repob, workspace_scores=True)
        report = await linker.link()
        assert report.workspace_scores_computed and not report.pagerank_available
        scores = await store.workspace_scores_for((("repob", "repob.fn"),))
        assert scores[("repob", "repob.fn")].pagerank is None
        assert scores[("repob", "repob.fn")].in_degree == 1


async def test_config_defaults_pinned() -> None:
    # AC31/AC34 config halves: defaults match the _DEFAULT_* single sources;
    # unknown keys raise; kinds validated against ReferenceKind.
    import pydantic
    import pytest as _pytest

    from pydocs_mcp.retrieval.config.models import CrossRepoConfig

    cfg = CrossRepoConfig()
    assert cfg.enabled is True  # AC34: default-on
    assert cfg.link_on_serve is True
    assert cfg.match_scope == "project_only"
    assert cfg.kinds == ("calls", "imports", "inherits", "governs")
    assert cfg.workspace_scores is True
    assert cfg.alias_resolution == "imports_graph"
    assert cfg.similar.top_k == 5 and cfg.similar.min_score == pytest.approx(0.6)
    with _pytest.raises(pydantic.ValidationError):
        CrossRepoConfig(mystery=True)  # type: ignore[call-arg]
    with _pytest.raises(pydantic.ValidationError, match="unknown reference kind"):
        CrossRepoConfig(kinds=("calls", "telepathy"))
    with _pytest.raises(pydantic.ValidationError):
        CrossRepoConfig(similar={"top_k": 5, "mystery": 1})
