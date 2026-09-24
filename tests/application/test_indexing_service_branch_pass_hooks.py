"""The ``IndexingService`` seams the branch pass goes through (#310): one insert
and embed path, a reference rewrite in the branch's own universe, and a node-
score recompute that leaves the served branch's dependency tier alone."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryBranchChunkStore,
    InMemoryBranchStore,
    InMemoryChunkStore,
    InMemoryNodeScoreStore,
    SpyVectorStore,
    make_fake_uow_factory,
)

_BRANCH = "feature/x"
_PROJECT = Package(
    name=PROJECT_PACKAGE_NAME,
    version="",
    summary="",
    homepage="",
    dependencies=(),
    content_hash="",
    origin=PackageOrigin.PROJECT,
)


def _chunk(text: str) -> Chunk:
    chunk = Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title=text, text=text)
    return replace(chunk, embedding=np.ones(4, dtype=np.float32))


async def test_persist_added_chunks_returns_ids_in_order_and_forwards_the_vectors() -> None:
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(vectors=vectors)
    service = IndexingService(uow_factory=factory)
    async with factory() as uow:
        ids = await service.persist_added_chunks(uow, _PROJECT, (_chunk("a"), _chunk("b")))
        assert await service.persist_added_chunks(uow, _PROJECT, ()) == ()
        await uow.commit()
    assert len(ids) == 2 and ids[0] < ids[1] and vectors.added == list(ids)


async def test_branch_references_resolve_against_the_branch_trees_only() -> None:
    factory = make_fake_uow_factory()
    tree = DocumentNode("pkg.b", "pkg.b", "b", NodeKind.MODULE, "pkg/b.py", 1, 2, "t", "h")
    ref = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.b", None, ReferenceKind.IMPORTS)
    service = IndexingService(uow_factory=factory)
    async with factory() as uow:
        # No ``packages`` row for the project (a bundle only branch passes
        # wrote): the branch's own trees still form its universe.
        await uow.trees.save_many((tree,), package=PROJECT_PACKAGE_NAME, branch=_BRANCH)
        await service.persist_references_for_branch(
            uow,
            references=(ref,),
            reference_aliases={},
            class_attribute_types={},
            branch=_BRANCH,
        )
        await uow.commit()
        edges = await uow.references.list_resolved((ReferenceKind.IMPORTS,), branch=_BRANCH)
        assert edges == [("pkg.a.f", "pkg.b")]
        assert await uow.references.list_resolved((ReferenceKind.IMPORTS,), branch="main") == []


async def test_a_branch_rescore_keeps_the_dependency_tier(monkeypatch) -> None:
    scores = InMemoryNodeScoreStore()
    factory = make_fake_uow_factory(node_scores=scores)
    kept_dependency = NodeScore("requests", "requests.get", pagerank=0.5)
    await scores.upsert((kept_dependency,), branch="")
    computed = (
        NodeScore(PROJECT_PACKAGE_NAME, "pkg.a", pagerank=0.9),
        NodeScore("requests", "requests.get", pagerank=0.1),
    )
    monkeypatch.setattr(node_score_compute, "compute_scores", lambda edges, packages: computed)
    service = IndexingService(uow_factory=factory, node_scores_enabled=True)
    await service.recompute_node_scores(branch=_BRANCH, replace_dependency_tier=False)
    assert scores.by_key == {("requests", "requests.get"): kept_dependency}
    assert scores.by_branch[_BRANCH] == {(PROJECT_PACKAGE_NAME, "pkg.a"): computed[0]}


def _scored_chunk(chunk_id: int, qname: str, package: str = PROJECT_PACKAGE_NAME) -> Chunk:
    return Chunk(text=qname, id=chunk_id, metadata={"package": package, "qualified_name": qname})


def _record(name: str, *, is_default: bool = False) -> BranchRecord:
    return BranchRecord(name, "0" * 40, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, is_default)


async def _scored_universe(monkeypatch, factory, branch: str | None) -> dict[str, str]:
    """The ``{qname: package}`` map one recompute hands the scorer."""
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(
        node_score_compute,
        "compute_scores",
        lambda edges, packages: seen.append(dict(packages)) or (),
    )
    await IndexingService(uow_factory=factory, node_scores_enabled=True).recompute_node_scores(
        branch=branch
    )
    return seen[0]


async def test_each_rescore_scores_only_the_project_chunks_its_branch_holds(monkeypatch) -> None:
    # #310: once a second branch is indexed the chunk table holds both
    # branches' project rows; each branch's graph scores its own.
    chunks, branches, membership = (
        InMemoryChunkStore(),
        InMemoryBranchStore(),
        InMemoryBranchChunkStore(),
    )
    factory = make_fake_uow_factory(chunks=chunks, branches=branches, branch_chunks=membership)
    shared, served_only, branch_only = (
        _scored_chunk(1, "pkg.shared"),
        _scored_chunk(2, "pkg.served_only"),
        _scored_chunk(3, "pkg.branch_only"),
    )
    await chunks.upsert((shared, served_only, branch_only, _scored_chunk(4, "dep.f", "dep")))
    await branches.upsert_branch(_record("main", is_default=True))
    await branches.upsert_branch(_record(_BRANCH))
    await membership.replace_membership(
        "main", (ChunkMembership("main", 1, "a.py"), ChunkMembership("main", 2, "a.py"))
    )
    await membership.replace_membership(
        _BRANCH, (ChunkMembership(_BRANCH, 1, "a.py"), ChunkMembership(_BRANCH, 3, "a.py"))
    )
    served = await _scored_universe(monkeypatch, factory, None)
    assert served == {
        "pkg.shared": PROJECT_PACKAGE_NAME,
        "pkg.served_only": PROJECT_PACKAGE_NAME,
        "dep.f": "dep",
    }
    on_branch = await _scored_universe(monkeypatch, factory, _BRANCH)
    assert on_branch == {
        "pkg.shared": PROJECT_PACKAGE_NAME,
        "pkg.branch_only": PROJECT_PACKAGE_NAME,
        "dep.f": "dep",
    }


async def test_a_bundle_without_branch_rows_scores_every_project_chunk(monkeypatch) -> None:
    # No git: the project chunks carry no membership, and all of them count.
    chunks = InMemoryChunkStore()
    await chunks.upsert((_scored_chunk(1, "pkg.a"), _scored_chunk(2, "pkg.b")))
    factory = make_fake_uow_factory(chunks=chunks)
    universe = await _scored_universe(monkeypatch, factory, None)
    assert universe == {"pkg.a": PROJECT_PACKAGE_NAME, "pkg.b": PROJECT_PACKAGE_NAME}


async def test_the_served_rescore_still_replaces_the_dependency_tier(monkeypatch) -> None:
    scores = InMemoryNodeScoreStore()
    factory = make_fake_uow_factory(node_scores=scores)
    await scores.upsert((NodeScore("requests", "requests.get", pagerank=0.5),), branch="")
    fresh = NodeScore("requests", "requests.get", pagerank=0.1)
    monkeypatch.setattr(node_score_compute, "compute_scores", lambda edges, packages: (fresh,))
    service = IndexingService(uow_factory=factory, node_scores_enabled=True)
    await service.recompute_node_scores(branch="main")
    assert scores.by_key == {("requests", "requests.get"): fresh}
