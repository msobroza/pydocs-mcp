"""run_branch_pass: one transaction that reuses cached files, inserts and embeds
only the misses' new chunks, writes the tree tier under the branch key, stamps a
git-objects branch row and lets the refcount GC reclaim (spec §6.3, #310)."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import numpy as np
import pytest

from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.branch_pass import (
    BranchExtraction,
    BranchPassInput,
    run_branch_pass,
)
from pydocs_mcp.application.extraction_cache import (
    EMPTY_SWEEP,
    CachedFile,
    FileArtifacts,
    ReferenceSweep,
)
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchStatus,
    Chunk,
    LandingKind,
    ModuleMember,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import SpyVectorStore, make_fake_uow_factory

_KEY = "p|x:k"
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


def _tree(module: str, path: str, *children: str) -> DocumentNode:
    kids = tuple(
        DocumentNode(f"{module}.{c}", f"{module}.{c}", c, NodeKind.FUNCTION, path, 1, 2, c, c)
        for c in children
    )
    return DocumentNode(
        module, module, module, NodeKind.MODULE, path, 1, 9, "t", "h", children=kids
    )


def _chunk(module: str, path: str, text: str, *, embedded: bool = True) -> Chunk:
    chunk = Chunk.from_test_inputs(
        package=PROJECT_PACKAGE_NAME,
        module=module,
        title=text,
        text=text,
        metadata={"source_path": path, "start_line": 1, "end_line": 2},
    )
    vector = np.ones(4, dtype=np.float32) if embedded else None
    return replace(chunk, embedding=vector)


def _member(module: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={"package": PROJECT_PACKAGE_NAME, "module": module, "name": name, "kind": "def"}
    )


def _manifest(name: str = _BRANCH, paths: tuple[str, ...] = ("pkg/a.py", "pkg/b.py")):
    return BranchManifest(
        name=name,
        head_sha="b" * 40,
        source=BranchIndexSource.GIT_OBJECTS,
        pipeline_hash="p",
        files=tuple(BranchFile(name, p, f"blob-{p}") for p in paths),
        base_name="main",
        merge_base_sha="a" * 40,
        base_tip_sha="c" * 40,
        extraction_cache_key=_KEY,
    )


def _extraction(
    chunks: tuple[Chunk, ...],
    trees: tuple[DocumentNode, ...],
    paths: tuple[str, ...],
    *,
    members: tuple[ModuleMember, ...] = (),
    references: tuple[NodeReference, ...] = (),
) -> BranchExtraction:
    result = ExtractionResult(chunks=chunks, trees=trees, package=_PROJECT, references=references)
    return BranchExtraction(result=result, members=members, paths=paths)


def _cached(chunk_id: int, tree: DocumentNode, *, sweep: ReferenceSweep = EMPTY_SWEEP):
    """A cache hit as ``cached_file`` restores it: members stamped with the branch."""
    membership = ChunkMembership(_BRANCH, chunk_id, tree.source_path, 1, 2)
    member = _member(tree.qualified_name, "f")
    stamped = ModuleMember(metadata={**member.metadata, "branch": _BRANCH})
    return CachedFile((membership,), FileArtifacts(tree, (stamped,), sweep))


def _decision(title: str, branch: str) -> DecisionRecord:
    return DecisionRecord(
        id=None,
        package=PROJECT_PACKAGE_NAME,
        title=title,
        status="active",
        source="adr",
        confidence=1.0,
        evidence=(),
        affected_files=(),
        affected_qnames=(),
        staleness_score=0.0,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=1.0,
        updated_at=1.0,
        branch=branch,
    )


async def _seed_main(factory, *texts: str) -> tuple[int, ...]:
    """``main`` is the served branch and owns one chunk per text in pkg/a.py."""
    async with factory() as uow:
        chunks = tuple(_chunk("pkg.a", "pkg/a.py", t) for t in texts)
        ids = await uow.chunks.insert_returning_ids(chunks)
        await uow.branches.upsert_branch(
            BranchRecord(
                "main",
                "a" * 40,
                BranchIndexSource.WORKING_TREE,
                "p",
                1.0,
                1.0,
                is_default=True,
                worktree_path="/repo",
            )
        )
        rows = [ChunkMembership("main", i, "pkg/a.py", 1, 2) for i in ids]
        await uow.branch_chunks.replace_membership("main", rows)
        await uow.commit()
    return ids


async def test_reuses_cached_files_and_embeds_only_the_new_chunks() -> None:
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(vectors=vectors)
    (shared,) = await _seed_main(factory, "same")
    extraction = _extraction(
        (_chunk("pkg.b", "pkg/b.py", "new"),),
        (_tree("pkg.b", "pkg/b.py", "g"),),
        ("pkg/b.py",),
        members=(_member("pkg.b", "g"),),
    )
    pass_input = BranchPassInput(
        _manifest(), (_cached(shared, _tree("pkg.a", "pkg/a.py", "f")),), extraction, now=5.0
    )
    outcome = await run_branch_pass(IndexingService(uow_factory=factory), factory, pass_input)
    assert (outcome.files_total, outcome.files_reused, outcome.files_extracted) == (2, 1, 1)
    assert (outcome.chunks_embedded, outcome.chunks_shared, outcome.vectors_removed) == (1, 1, 0)
    async with factory() as uow:
        rows = await uow.branch_chunks.list_membership(_BRANCH)
        (new_id,) = {m.chunk_id for m in rows} - {shared}
        assert vectors.added == [new_id]
        assert {m.source_path for m in rows} == {"pkg/a.py", "pkg/b.py"}
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert await uow.file_extractions.get("blob-pkg/b.py", "pkg/b.py", _KEY) is not None
        assert await uow.file_extractions.get("blob-pkg/a.py", "pkg/a.py", _KEY) is None


async def test_the_branch_row_is_a_git_objects_row_with_its_base() -> None:
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    pass_input = BranchPassInput(_manifest(paths=()), (), None, now=5.0)
    await run_branch_pass(IndexingService(uow_factory=factory), factory, pass_input)
    async with factory() as uow:
        record = await uow.branches.get_branch(_BRANCH)
        assert record.source is BranchIndexSource.GIT_OBJECTS and record.is_default is False
        assert (record.base_name, record.merge_base_sha) == ("main", "a" * 40)
        assert record.worktree_path is None and record.head_sha == "b" * 40
        assert await uow.branches.default_branch_name() == "main"


async def test_the_tree_tier_is_written_under_the_branch_and_main_is_untouched() -> None:
    factory = make_fake_uow_factory()
    (shared,) = await _seed_main(factory, "same")
    async with factory() as uow:
        await uow.trees.save_many(
            (_tree("pkg.a", "pkg/a.py"),), package=PROJECT_PACKAGE_NAME, branch="main"
        )
        await uow.commit()
    extraction = _extraction(
        (_chunk("pkg.b", "pkg/b.py", "new"),),
        (_tree("pkg.b", "pkg/b.py", "g"),),
        ("pkg/b.py",),
        members=(_member("pkg.b", "g"),),
    )
    cached = (_cached(shared, _tree("pkg.a", "pkg/a.py", "f")),)
    await run_branch_pass(
        IndexingService(uow_factory=factory),
        factory,
        BranchPassInput(_manifest(), cached, extraction, now=5.0),
    )
    async with factory() as uow:
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.b", branch=_BRANCH) is not None
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.a", branch=_BRANCH) is not None
        # The served read (branch=None) still answers from main only.
        assert await uow.trees.load(PROJECT_PACKAGE_NAME, "pkg.b") is None
        # The branch filter matches exactly, so both members carry the branch
        # (reads keep the branch column out of member metadata).
        members = await uow.module_members.list(
            filter={"package": PROJECT_PACKAGE_NAME, "branch": _BRANCH}
        )
        assert sorted(m.metadata["name"] for m in members) == ["f", "g"]


async def test_a_miss_sharing_a_hash_with_a_cached_file_gets_its_own_row() -> None:
    """Membership is keyed (branch, chunk_id): the diff pool excludes the ids the
    cached files already claim, so a duplicate text is inserted, never
    assigned twice."""
    factory = make_fake_uow_factory()
    (shared,) = await _seed_main(factory, "same")
    twin = _chunk("pkg.a", "pkg/c.py", "same")
    extraction = _extraction((twin,), (_tree("pkg.c", "pkg/c.py"),), ("pkg/c.py",))
    cached = (_cached(shared, _tree("pkg.a", "pkg/a.py")),)
    pass_input = BranchPassInput(
        _manifest(paths=("pkg/a.py", "pkg/c.py")), cached, extraction, now=5.0
    )
    await run_branch_pass(IndexingService(uow_factory=factory), factory, pass_input)
    async with factory() as uow:
        ids = [m.chunk_id for m in await uow.branch_chunks.list_membership(_BRANCH)]
    assert len(ids) == len(set(ids)) == 2 and shared in ids


async def test_references_resolve_in_the_branch_universe_and_similar_edges_carry_over() -> None:
    factory = make_fake_uow_factory()
    (shared,) = await _seed_main(factory, "same")
    similar = (
        NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.a.g", "pkg.a.g", ReferenceKind.SIMILAR),
        NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.z.h", "pkg.z.h", ReferenceKind.SIMILAR),
    )
    async with factory() as uow:
        await uow.references.save_many(similar, package=PROJECT_PACKAGE_NAME, branch="main")
        await uow.commit()
    calls = NodeReference(PROJECT_PACKAGE_NAME, "pkg.a.f", "pkg.b.g", None, ReferenceKind.CALLS)
    sweep = ReferenceSweep((calls,), {}, {})
    cached = (_cached(shared, _tree("pkg.a", "pkg/a.py", "f", "g"), sweep=sweep),)
    extraction = _extraction(
        (_chunk("pkg.b", "pkg/b.py", "new"),), (_tree("pkg.b", "pkg/b.py", "g"),), ("pkg/b.py",)
    )
    await run_branch_pass(
        IndexingService(uow_factory=factory),
        factory,
        BranchPassInput(_manifest(), cached, extraction, now=5.0),
    )
    async with factory() as uow:
        kinds = (ReferenceKind.CALLS, ReferenceKind.SIMILAR)
        edges = set(await uow.references.list_resolved(kinds, branch=_BRANCH))
        served = set(await uow.references.list_resolved(kinds))
    assert ("pkg.a.f", "pkg.b.g") in edges and ("pkg.a.f", "pkg.a.g") in edges
    # The neighbour the branch does not hold is not carried; main is untouched.
    assert ("pkg.a.f", "pkg.z.h") not in edges
    assert served == {(r.from_node_id, r.to_node_id) for r in similar}


async def test_a_re_run_keeps_the_branchs_own_similar_edges_between_reused_files() -> None:
    """Files that were misses on the previous pass got fresh kNN edges then; hits
    now, they keep them from the branch's own rows — main never held them."""
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    own = NodeReference(
        PROJECT_PACKAGE_NAME, "pkg.b.g", "pkg.c.h", "pkg.c.h", ReferenceKind.SIMILAR
    )
    async with factory() as uow:
        b_id, c_id = await uow.chunks.insert_returning_ids(
            (_chunk("pkg.b", "pkg/b.py", "b"), _chunk("pkg.c", "pkg/c.py", "c"))
        )
        await uow.references.save_many((own,), package=PROJECT_PACKAGE_NAME, branch=_BRANCH)
        await uow.commit()
    cached = (
        _cached(b_id, _tree("pkg.b", "pkg/b.py", "g")),
        _cached(c_id, _tree("pkg.c", "pkg/c.py", "h")),
    )
    await run_branch_pass(
        IndexingService(uow_factory=factory),
        factory,
        BranchPassInput(_manifest(paths=("pkg/b.py", "pkg/c.py")), cached, None, now=5.0),
    )
    async with factory() as uow:
        edges = await uow.references.list_resolved((ReferenceKind.SIMILAR,), branch=_BRANCH)
    assert ("pkg.b.g", "pkg.c.h") in edges


async def test_the_branch_carries_no_decision_rows() -> None:
    """O10 (decision mining per branch) is P2: a stale row goes, none is written."""
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    async with factory() as uow:
        await uow.decisions.upsert((_decision("stale", _BRANCH),))
        await uow.commit()
    await run_branch_pass(
        IndexingService(uow_factory=factory),
        factory,
        BranchPassInput(_manifest(paths=()), (), None, now=5.0),
    )
    async with factory() as uow:
        rows = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME, branch=_BRANCH)
    assert rows == ()


async def test_a_second_pass_frees_the_replaced_chunk_and_its_vector() -> None:
    vectors = SpyVectorStore()
    factory = make_fake_uow_factory(vectors=vectors)
    (shared,) = await _seed_main(factory, "same")
    service = IndexingService(uow_factory=factory)
    cached = (_cached(shared, _tree("pkg.a", "pkg/a.py")),)
    tree_b = (_tree("pkg.b", "pkg/b.py"),)
    first = _extraction((_chunk("pkg.b", "pkg/b.py", "old"),), tree_b, ("pkg/b.py",))
    await run_branch_pass(service, factory, BranchPassInput(_manifest(), cached, first, now=5.0))
    old_id = vectors.added[-1]
    second = _extraction((_chunk("pkg.b", "pkg/b.py", "newer"),), tree_b, ("pkg/b.py",))
    outcome = await run_branch_pass(
        service, factory, BranchPassInput(_manifest(), cached, second, now=6.0)
    )
    assert outcome.vectors_removed == 1 and vectors.removed == [old_id]
    async with factory() as uow:
        assert await uow.branch_chunks.count_for_branch("main") == 1
        assert shared in {m.chunk_id for m in await uow.branch_chunks.list_membership(_BRANCH)}


async def test_a_pass_reactivates_a_retired_row_and_keeps_its_pin() -> None:
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    retired = BranchRecord(
        _BRANCH,
        "e" * 40,
        BranchIndexSource.GIT_OBJECTS,
        "p",
        1.0,
        1.0,
        status=BranchStatus.INACTIVE,
        retired_at=2.0,
        purge_after=3.0,
        pinned=True,
    )
    async with factory() as uow:
        await uow.branches.upsert_branch(retired)
        await uow.commit()
    await run_branch_pass(
        IndexingService(uow_factory=factory),
        factory,
        BranchPassInput(_manifest(paths=()), (), None, now=5.0),
    )
    async with factory() as uow:
        record = await uow.branches.get_branch(_BRANCH)
    assert record.status is BranchStatus.ACTIVE and record.pinned is True
    assert (record.retired_at, record.purge_after) == (None, None)


async def test_the_served_branch_is_refused_and_nothing_is_written() -> None:
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    with pytest.raises(ValueError, match="served branch 'main'"):
        await run_branch_pass(
            IndexingService(uow_factory=factory),
            factory,
            BranchPassInput(_manifest(name="main", paths=()), (), None, now=5.0),
        )
    async with factory() as uow:
        assert (await uow.branches.get_branch("main")).is_default is True
        assert await uow.branch_chunks.count_for_branch("main") == 1


async def test_a_landing_unit_is_refused_and_nothing_is_written() -> None:
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    unit = BranchRecord(
        _BRANCH,
        "e" * 40,
        BranchIndexSource.GIT_OBJECTS,
        "p",
        1.0,
        1.0,
        landing_kind=LandingKind.SINGLE_COMMIT,
    )
    async with factory() as uow:
        await uow.branches.upsert_branch(unit)
        await uow.commit()
    with pytest.raises(ValueError, match="landing unit"):
        await run_branch_pass(
            IndexingService(uow_factory=factory),
            factory,
            BranchPassInput(_manifest(paths=()), (), None, now=5.0),
        )
    async with factory() as uow:
        assert await uow.branches.get_branch(_BRANCH) == unit


async def test_the_pass_logs_one_branch_reindex_event(caplog: pytest.LogCaptureFixture) -> None:
    factory = make_fake_uow_factory()
    await _seed_main(factory, "same")
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await run_branch_pass(
            IndexingService(uow_factory=factory),
            factory,
            BranchPassInput(_manifest(paths=()), (), None, now=5.0),
        )
    events = [json.loads(r.message) for r in caplog.records if "branch_reindex" in r.message]
    assert events == [
        {
            "event": "branch_reindex",
            "branch": _BRANCH,
            "files_total": 0,
            "files_reused": 0,
            "files_extracted": 0,
            "chunks_embedded": 0,
            "chunks_shared": 0,
            "vectors_removed": 0,
        }
    ]
