"""The write path keys the tree tier by branch (spec §6.1 v18, #307).

A working-tree pass stamps the manifest's branch on every project row of the
five tree-tier tables, replaces that branch's rows and the unbranched copy an
earlier pass left, and — when a checkout retires the previous branch of the
same worktree — purges that branch's rows in the same transaction. Dependency
packages keep the dependency tier (``branch = ''``). Driven through the
in-memory fakes, which follow the same branch rule as the SQLite stores.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application import node_score_compute
from pydocs_mcp.application.branch_manifest import BranchManifest
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    Chunk,
    ModuleMember,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryDecisionStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

PROJECT = PROJECT_PACKAGE_NAME


class _TreeTier:
    """The five tree-tier fakes, wired into one fake unit-of-work factory."""

    def __init__(self) -> None:
        self.trees = InMemoryDocumentTreeStore()
        self.members = InMemoryModuleMemberStore()
        self.references = InMemoryReferenceStore()
        self.scores = InMemoryNodeScoreStore()
        self.decisions = InMemoryDecisionStore()
        self.factory = make_fake_uow_factory(
            trees=self.trees,
            module_members=self.members,
            references=self.references,
            node_scores=self.scores,
            decisions=self.decisions,
        )
        self.service = IndexingService(uow_factory=self.factory, node_scores_enabled=True)


def _package(name: str = PROJECT, origin: PackageOrigin = PackageOrigin.PROJECT) -> Package:
    return Package(
        name=name,
        version="0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


def _manifest(name: str) -> BranchManifest:
    return BranchManifest(
        name=name,
        head_sha="c" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        files=(),
        worktree_path="/repo",
        extraction_cache_key="p|x:k",
    )


def _chunk(package: str, text: str) -> Chunk:
    return Chunk.from_test_inputs(package=package, module="pkg.a", title=text, text=text)


def _tree(module: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path="pkg/a.py",
        start_line=1,
        end_line=1,
        text=module,
        content_hash=module,
    )


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={"package": package, "module": "pkg.a", "name": name, "kind": "def"}
    )


def _ref(package: str, source: str, target: str) -> NodeReference:
    return NodeReference(package, source, target, None, ReferenceKind.CALLS)


def _raw_decision(title: str) -> RawDecision:
    return RawDecision(
        title=title,
        status="active",
        source="adr",
        confidence=1.0,
        evidence=(),
        affected_files=(),
        affected_qnames=(),
        evidence_date=5.0,
    )


async def _index_project(tier: _TreeTier, branch: str | None, module: str = "pkg.a") -> None:
    await tier.service.reindex_package(
        _package(),
        (_chunk(PROJECT, module),),
        (_member(PROJECT, module),),
        trees=(_tree(module),),
        references=(_ref(PROJECT, module, "requests.get"),),
        decisions=(_raw_decision("Use SQLite"),),
        branch_manifest=_manifest(branch) if branch is not None else None,
    )


async def _index_dependency(tier: _TreeTier) -> None:
    await tier.service.reindex_package(
        _package("requests", PackageOrigin.DEPENDENCY),
        (_chunk("requests", "requests.api"),),
        (_member("requests", "get"),),
        trees=(_tree("requests.get"),),
        references=(_ref("requests", "requests.get", "requests.adapters.send"),),
    )


async def test_a_working_tree_pass_stamps_every_project_row_with_its_branch() -> None:
    tier = _TreeTier()
    await _index_project(tier, "main")
    assert tier.trees.by_package == {}  # no unbranched copy
    assert [t.qualified_name for t in tier.trees.by_branch["main"][PROJECT]] == ["pkg.a"]
    assert [r.from_node_id for r in tier.references.by_branch["main"][PROJECT]] == ["pkg.a"]
    assert [m.metadata["branch"] for m in tier.members.by_package[PROJECT]] == ["main"]
    assert [d.branch for d in tier.decisions.by_id.values()] == ["main"]
    async with tier.factory() as uow:
        # '' reads the dependency tier only; None the served default branch.
        assert await uow.trees.load(PROJECT, "pkg.a", branch="") is None
        assert await uow.trees.load(PROJECT, "pkg.a") is not None
        assert await uow.decisions.list_for_package(PROJECT, branch="") == ()
        assert len(await uow.decisions.list_for_package(PROJECT)) == 1


async def test_a_dependency_pass_writes_the_dependency_tier_every_branch_reads() -> None:
    tier = _TreeTier()
    await _index_project(tier, "main")
    await _index_dependency(tier)
    assert [t.qualified_name for t in tier.trees.by_package["requests"]] == ["requests.get"]
    assert "requests" in tier.references.by_package
    assert [m.metadata.get("branch", "") for m in tier.members.by_package["requests"]] == [""]
    async with tier.factory() as uow:
        for branch in ("", "main", "feature/x", None):
            assert await uow.trees.load("requests", "requests.get", branch=branch) is not None
        # The dependency pass resolved the project's pending edge on every branch.
        callees = await uow.references.find_callees(from_node_id="pkg.a", branch="main")
    assert [r.to_node_id for r in callees] == ["requests.get"]


async def test_the_same_symbol_on_two_branches_keeps_two_rows_keyed_by_branch() -> None:
    tier = _TreeTier()
    async with tier.factory() as uow:
        await uow.trees.save_many([_tree("pkg.a")], package=PROJECT, branch="main")
        await uow.trees.save_many([_tree("pkg.a")], package=PROJECT, branch="feature/x")
        await uow.commit()
    assert set(tier.trees.by_branch) == {"main", "feature/x"}
    async with tier.factory() as uow:
        assert await uow.trees.exists(PROJECT, "pkg.a", branch="feature/x") is True
        await uow.trees.delete_for_package(PROJECT, branch="main")
        assert await uow.trees.load(PROJECT, "pkg.a", branch="main") is None
        assert await uow.trees.load(PROJECT, "pkg.a", branch="feature/x") is not None


async def test_a_working_tree_pass_clears_the_unbranched_copy_an_earlier_pass_left() -> None:
    """Rows a pass without a manifest wrote under '' (what the write path did
    before #307) must not survive beside the stamped rows: readers of
    ``branch IN (?, '')`` would serve both copies."""
    tier = _TreeTier()
    await _index_project(tier, None)
    assert PROJECT in tier.trees.by_package
    (decision_id,) = tier.decisions.by_id
    await _index_project(tier, "main")
    assert PROJECT not in tier.trees.by_package
    assert PROJECT not in tier.references.by_package
    assert [m.metadata["branch"] for m in tier.members.by_package[PROJECT]] == ["main"]
    # Reconciled in place: the id kept chunks point at survives, re-stamped.
    assert [(d.id, d.branch) for d in tier.decisions.by_id.values()] == [(decision_id, "main")]


async def test_a_checkout_switch_purges_the_previous_branch_and_carries_its_decisions() -> None:
    tier = _TreeTier()
    await _index_project(tier, "main")
    (decision_id,) = tier.decisions.by_id
    await _index_project(tier, "feature/x", module="pkg.b")
    assert tier.trees.by_branch.get("main", {}) == {}
    assert tier.references.by_branch.get("main", {}) == {}
    assert [m.metadata["branch"] for m in tier.members.by_package[PROJECT]] == ["feature/x"]
    # Decisions follow the working tree (spec §11 O10): same id, new stamp —
    # a kept decision chunk's decision_id still names its record.
    assert [(d.id, d.branch) for d in tier.decisions.by_id.values()] == [(decision_id, "feature/x")]
    async with tier.factory() as uow:
        assert await uow.trees.load(PROJECT, "pkg.a") is None
        assert (await uow.trees.load(PROJECT, "pkg.b")).qualified_name == "pkg.b"


async def test_a_checkout_switch_purges_the_previous_branch_scores() -> None:
    tier = _TreeTier()
    await _index_project(tier, "main")
    async with tier.factory() as uow:
        await uow.node_scores.upsert([NodeScore(PROJECT, "pkg.a")], branch="main")
        await uow.commit()
    await _index_project(tier, "feature/x")
    assert tier.scores.by_branch.get("main", {}) == {}


def _fixed_scores(edges, qname_packages) -> list[NodeScore]:
    return [NodeScore(PROJECT, "pkg.a", in_degree=2), NodeScore("requests", "requests.get")]


async def test_node_scores_stamp_project_rows_and_keep_dependency_rows_unbranched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_score_compute, "compute_scores", _fixed_scores)
    tier = _TreeTier()
    await _index_project(tier, "main")
    stale = {(PROJECT, "pkg.gone"): NodeScore(PROJECT, "pkg.gone")}
    tier.scores.by_key.update(stale)  # an unbranched project row an older pass left
    await tier.service.recompute_node_scores(branch="main")
    assert set(tier.scores.by_branch["main"]) == {(PROJECT, "pkg.a")}
    assert set(tier.scores.by_key) == {("requests", "requests.get")}


async def test_node_scores_replace_clears_the_whole_dependency_tier_in_one_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The '' tier is swapped whole, like the pre-branch ``delete_all``: a row
    of a package no longer in ``packages`` goes too, with one delete, not one
    per package."""
    monkeypatch.setattr(node_score_compute, "compute_scores", _fixed_scores)
    tier = _TreeTier()
    await _index_project(tier, "main")
    tier.scores.by_key[("gone", "gone.x")] = NodeScore("gone", "gone.x")
    tier.scores.calls.clear()
    await tier.service.recompute_node_scores(branch="main")
    assert set(tier.scores.by_key) == {("requests", "requests.get")}
    deletes = [(c.method, c.branch) for c in tier.scores.calls if c.method.startswith("delete")]
    assert deletes == [("delete_for_branch", ""), ("delete_for_package", "main")]


async def test_node_scores_without_a_branch_stamp_the_served_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(node_score_compute, "compute_scores", _fixed_scores)
    tier = _TreeTier()
    await _index_project(tier, "main")
    await tier.service.recompute_node_scores()
    assert set(tier.scores.by_branch["main"]) == {(PROJECT, "pkg.a")}


async def test_decision_rows_of_a_dependency_are_reconciled_on_the_dependency_tier() -> None:
    tier = _TreeTier()
    record = DecisionRecord(
        id=None,
        package="requests",
        title="stale",
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
    )
    await tier.decisions.upsert([record])
    await _index_dependency(tier)
    assert tier.decisions.by_id == {}  # decisions=() reconciles the old row away


async def test_mined_dependency_decisions_stay_in_the_dependency_tier_across_a_checkout() -> None:
    """Under ``decision_capture.include_deps`` a dependency carries decisions
    (#346): stamped '' like its other rows, read on every branch, and left alone
    by a checkout switch, whose purge drops only the project's rows."""
    tier = _TreeTier()
    await _index_project(tier, "main")
    await tier.service.reindex_package(
        _package("requests", PackageOrigin.DEPENDENCY),
        (_chunk("requests", "requests.api"),),
        (),
        decisions=(_raw_decision("Retry idempotent verbs"),),
    )
    await _index_project(tier, "feature/x", module="pkg.b")
    assert sorted((d.package, d.branch) for d in tier.decisions.by_id.values()) == [
        (PROJECT, "feature/x"),
        ("requests", ""),
    ]
    async with tier.factory() as uow:
        for branch in ("", "main", "feature/x", None):
            records = await uow.decisions.list_for_package("requests", branch=branch)
            assert [r.title for r in records] == ["Retry idempotent verbs"]
