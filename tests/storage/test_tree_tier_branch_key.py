"""Spec §6.1 v18 (#307): the tree tier is keyed by branch.

Writes stamp exactly the given branch. Reads take ``branch: str | None``:
``None`` reads the served default branch (``branches.is_default``), ``''`` the
dependency tier only, a name that branch — each plus the branch-agnostic
dependency rows (``branch = ''``). Deletes take one branch, or ``None`` for
every branch.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import BranchIndexSource, ModuleMember
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.decision_record import DecisionRecord
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from pydocs_mcp.storage.sqlite import (
    SqliteBranchRepository,
    SqliteDecisionRepository,
    SqliteDocumentTreeStore,
    SqliteModuleMemberRepository,
    SqliteNodeScoreRepository,
    SqliteReferenceStore,
)
from tests._fakes import InMemoryModuleMemberStore

PROJECT = "__project__"


def _tree(module: str, text: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=1,
        text=text,
        content_hash=text,
    )


def _calls(from_package: str, source: str, target: str) -> NodeReference:
    return NodeReference(
        from_package=from_package,
        from_node_id=source,
        to_name=target,
        to_node_id=target,
        kind=ReferenceKind.CALLS,
    )


def _member(name: str, branch: str | None = None) -> ModuleMember:
    metadata = {"package": PROJECT, "module": "pkg.a", "name": name, "kind": "def"}
    if branch is not None:
        metadata["branch"] = branch
    return ModuleMember(metadata=metadata)


def _decision(title: str, branch: str = "") -> DecisionRecord:
    return DecisionRecord(
        id=None,
        package=PROJECT,
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


@pytest.fixture
def provider(tmp_path: Path):
    db = tmp_path / "t.db"
    open_index_database(db).close()
    return build_connection_provider(db)


async def _serve_default_branch(provider, name: str) -> None:
    record = BranchRecord(
        name=name,
        head_sha="a" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=1.0,
        last_used_at=1.0,
        is_default=True,
    )
    await SqliteBranchRepository(provider=provider).upsert_branch(record)


async def test_trees_are_isolated_per_branch_and_dependency_rows_are_shared(provider) -> None:
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([_tree("pkg.a", "main")], package=PROJECT, branch="main")
    await store.save_many([_tree("pkg.a", "feature")], package=PROJECT, branch="feature/x")
    await store.save_many([_tree("requests.api", "dep")], package="requests")
    assert (await store.load(PROJECT, "pkg.a", branch="main")).text == "main"
    assert (await store.load(PROJECT, "pkg.a", branch="feature/x")).text == "feature"
    assert await store.load(PROJECT, "pkg.a", branch="") is None  # '' sees no project row
    assert (await store.load("requests", "requests.api", branch="feature/x")).text == "dep"
    assert (await store.load("requests", "requests.api", branch="")).text == "dep"
    assert await store.exists(PROJECT, "pkg.a", branch="main") is True
    assert await store.exists(PROJECT, "pkg.a", branch="") is False
    assert set(await store.load_all_in_package(PROJECT, branch="main")) == {"pkg.a"}
    await store.delete_for_package(PROJECT, branch="feature/x")
    assert await store.load(PROJECT, "pkg.a", branch="feature/x") is None
    assert await store.load(PROJECT, "pkg.a", branch="main") is not None
    await store.delete_for_package(PROJECT)  # None: every branch
    assert await store.load(PROJECT, "pkg.a", branch="main") is None
    assert await store.load("requests", "requests.api", branch="") is not None


async def test_a_branch_row_wins_over_an_unbranched_copy_of_the_same_module(provider) -> None:
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([_tree("pkg.a", "unbranched")], package=PROJECT)
    await store.save_many([_tree("pkg.a", "main")], package=PROJECT, branch="main")
    assert (await store.load(PROJECT, "pkg.a", branch="main")).text == "main"
    assert (await store.load(PROJECT, "pkg.a", branch="")).text == "unbranched"


async def test_a_read_without_a_branch_serves_the_default_branch(provider) -> None:
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([_tree("pkg.a", "main")], package=PROJECT, branch="main")
    await store.save_many([_tree("pkg.b", "feature")], package=PROJECT, branch="feature/x")
    await store.save_many([_tree("requests.api", "dep")], package="requests")
    # No branches row yet: the served branch is '' — the dependency tier only.
    assert await store.load(PROJECT, "pkg.a") is None
    assert await store.load("requests", "requests.api") is not None
    await _serve_default_branch(provider, "main")
    assert (await store.load(PROJECT, "pkg.a")).text == "main"
    assert await store.load(PROJECT, "pkg.b") is None
    assert await store.exists(PROJECT, "pkg.a") is True
    assert set(await store.load_all_in_package(PROJECT)) == {"pkg.a"}


async def test_references_read_the_branch_plus_the_dependency_tier(provider) -> None:
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_calls(PROJECT, "pkg.a.f", "pkg.b.g")], package=PROJECT, branch="main")
    on_feature = _calls(PROJECT, "pkg.a.h", "pkg.b.g")
    await store.save_many([on_feature], package=PROJECT, branch="feature/x")
    await store.save_many([_calls("requests", "requests.api.get", "pkg.b.g")], package="requests")

    async def callers(branch: str | None) -> set[str]:
        rows = await store.find_callers(target_node_id="pkg.b.g", branch=branch)
        return {r.from_node_id for r in rows}

    assert await callers("main") == {"pkg.a.f", "requests.api.get"}
    assert await callers("") == {"requests.api.get"}
    assert await callers(None) == {"requests.api.get"}  # no default branch served yet
    await _serve_default_branch(provider, "feature/x")
    assert await callers(None) == {"pkg.a.h", "requests.api.get"}
    transitive = await store.find_transitive_callers("pkg.b.g", max_depth=2, branch="feature/x")
    assert {row[0] for row in transitive} == {"pkg.a.h", "requests.api.get"}
    assert {e[0] for e in await store.resolved_edges(branch="main")} == {
        "pkg.a.f",
        "requests.api.get",
    }
    await store.delete_for_package(PROJECT, branch="main")
    assert await callers("main") == {"requests.api.get"}
    assert await callers("feature/x") == {"pkg.a.h", "requests.api.get"}


async def test_a_transitive_walk_never_crosses_into_another_branch(provider) -> None:
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_calls(PROJECT, "pkg.a", "pkg.b")], package=PROJECT, branch="main")
    await store.save_many([_calls(PROJECT, "pkg.b", "pkg.c")], package=PROJECT, branch="other")
    callees = await store.find_transitive_callees("pkg.a", max_depth=3, branch="main")
    assert [row[0] for row in callees] == ["pkg.b"]


async def test_unresolved_rows_resolve_per_branch_or_on_every_branch(provider) -> None:
    store = SqliteReferenceStore(provider=provider)
    unresolved = NodeReference(PROJECT, "pkg.a.f", "dep.x", None, ReferenceKind.CALLS)
    await store.save_many([unresolved], package=PROJECT, branch="main")
    await store.save_many([unresolved], package=PROJECT, branch="other")
    kinds = (ReferenceKind.CALLS,)
    assert len(await store.list_unresolved(kinds, branch="main")) == 1
    assert await store.resolve_unresolved({"dep.x"}, branch="main") == 1
    assert await store.list_unresolved(kinds, branch="main") == []
    assert len(await store.list_unresolved(kinds, branch="other")) == 1
    # None: a dependency's qnames resolve the rows of every branch.
    assert await store.resolve_unresolved({"dep.x"}) == 1
    assert await store.list_unresolved(kinds, branch="other") == []


async def test_scores_are_keyed_by_branch(provider) -> None:
    scores = SqliteNodeScoreRepository(provider=provider)
    await scores.upsert([NodeScore(PROJECT, "pkg.a.f", 1, 0.5, 0)], branch="main")
    await scores.upsert([NodeScore(PROJECT, "pkg.a.f", 9, 0.9, 1)], branch="feature/x")
    await scores.upsert([NodeScore("requests", "requests.get", 3, 0.1, 2)])
    assert (await scores.scores_for(["pkg.a.f"], branch="main"))["pkg.a.f"].in_degree == 1
    assert [s.in_degree for s in await scores.for_package(PROJECT, branch="feature/x")] == [9]
    assert await scores.for_package(PROJECT, branch="") == []
    assert set(await scores.scores_for(["pkg.a.f", "requests.get"], branch="")) == {"requests.get"}
    assert set(await scores.community_cohesion(PROJECT, branch="main")) == {0}
    await scores.delete_for_package(PROJECT, branch="main")
    assert await scores.for_package(PROJECT, branch="main") == []
    assert len(await scores.for_package(PROJECT, branch="feature/x")) == 1
    await scores.delete_for_package(PROJECT)
    assert await scores.for_package(PROJECT, branch="feature/x") == []


async def test_a_tier_delete_drops_one_branch_of_every_package(provider) -> None:
    scores = SqliteNodeScoreRepository(provider=provider)
    await scores.upsert([NodeScore("requests", "requests.get"), NodeScore("gone", "gone.x")])
    await scores.upsert([NodeScore(PROJECT, "pkg.a")], branch="main")
    await scores.delete_for_branch("")
    wanted = ["requests.get", "gone.x", "pkg.a"]
    assert set(await scores.scores_for(wanted, branch="main")) == {"pkg.a"}


async def test_members_carry_their_branch_as_a_filter_column(provider) -> None:
    members = SqliteModuleMemberRepository(provider=provider)
    await members.upsert_many([_member("f", "main"), _member("f", "feature/x"), _member("g")])
    assert await members.count(filter={"package": PROJECT, "branch": "main"}) == 1
    assert await members.count(filter={"package": PROJECT, "branch": ""}) == 1
    assert await members.count(filter={"package": PROJECT}) == 3
    assert await members.delete(filter={"package": PROJECT, "branch": "feature/x"}) == 1
    assert await members.count(filter={"package": PROJECT}) == 2


async def test_a_member_read_keeps_the_branch_out_of_metadata_on_both_stores(provider) -> None:
    """The branch is a column, not member metadata: member metadata reaches
    rendering, so the fake returns what the SQLite mapper returns."""
    for store in (SqliteModuleMemberRepository(provider=provider), InMemoryModuleMemberStore()):
        await store.upsert_many([_member("f", "main")])
        listed = await store.list(filter={"package": PROJECT, "branch": "main"})
        assert ["branch" in m.metadata for m in listed] == [False], type(store).__name__


async def test_decisions_are_keyed_by_branch(provider) -> None:
    decisions = SqliteDecisionRepository(provider=provider)
    await decisions.upsert([_decision("on main", "main"), _decision("shared")])
    listed = await decisions.list_for_package(PROJECT, branch="main")
    assert [(r.title, r.branch) for r in listed] == [("on main", "main"), ("shared", "")]
    assert [r.title for r in await decisions.list_for_package(PROJECT, branch="")] == ["shared"]
    assert [r.title for r in await decisions.list_for_package(PROJECT, branch="x")] == ["shared"]
    await _serve_default_branch(provider, "main")
    assert len(await decisions.list_for_package(PROJECT)) == 2
    # An update rewrites the branch a record carries (a reconcile re-stamps its rows).
    await decisions.upsert([replace(listed[0], branch="feature/x")])
    on_main = await decisions.list_for_package(PROJECT, branch="main")
    assert [r.title for r in on_main] == ["shared"]
    await decisions.delete_for_package(PROJECT, branch="feature/x")
    on_feature = await decisions.list_for_package(PROJECT, branch="feature/x")
    assert [r.title for r in on_feature] == ["shared"]


async def test_branch_filters_keep_the_row_order_of_the_indexes_readers_used(provider) -> None:
    """The unary ``+`` in the read clause keeps the planner off the branch-led
    primary keys: rows come back in the order the pre-v18 indexes gave them
    (rowid order within the seek), not sorted by the key — and that order
    reaches the answers (#307 byte identity)."""
    scores = SqliteNodeScoreRepository(provider=provider)
    ordered = [NodeScore(PROJECT, qname) for qname in ("pkg.z", "pkg.a", "pkg.m")]
    await scores.upsert(ordered, branch="main")
    listed = await scores.for_package(PROJECT, branch="main")
    assert [s.qualified_name for s in listed] == ["pkg.z", "pkg.a", "pkg.m"]
    refs = SqliteReferenceStore(provider=provider)
    edges = [_calls("zeta", "pkg.f", "pkg.x"), _calls("alpha", "pkg.f", "pkg.y")]
    await refs.save_many(edges, package="zeta")
    callees = await refs.find_callees(from_node_id="pkg.f", branch="")
    assert [r.from_package for r in callees] == ["zeta", "alpha"]
