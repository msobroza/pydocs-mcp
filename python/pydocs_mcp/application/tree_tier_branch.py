"""Which branch one package rewrite stamps, reads and clears in the tree tier.

Spec §6.1 v18 (#307): the five tree-tier tables (``document_trees``,
``module_members``, ``node_references``, ``node_scores``, ``decision_records``)
are keyed by branch. A working-tree pass of the project stamps its manifest's
branch; dependency packages — and a project pass without a manifest — write the
branch-agnostic dependency tier (``''``) and keep the pre-branch package-wide
replace. Functions over an OPEN ``uow``, kept out of ``indexing_service.py``
(§6.14 item 2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import groupby
from typing import TYPE_CHECKING

from pydocs_mcp.application.branch_membership import branches_retired_by
from pydocs_mcp.models import (
    DEPENDENCY_TIER,
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
)

# The chunk metadata key naming the symbol a chunk renders (no filter field).
_QUALIFIED_NAME_KEY = "qualified_name"

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_manifest import BranchManifest
    from pydocs_mcp.storage.decision_record import DecisionRecord
    from pydocs_mcp.storage.node_score import NodeScore
    from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class TreeTierBranchScope:
    """The branch key of one ``reindex_package`` pass.

    ``stamp`` is the branch every write stamps. ``view`` is the branch the
    pass's own reads see (resolver universe, cross-package re-resolution);
    ``None`` reads what the bundle serves. ``cleared`` names the branches whose
    rows of the package the rewrite replaces, ``None`` meaning every branch.
    ``carried`` names the retired branches whose decisions the pass takes over.
    """

    stamp: str
    view: str | None
    cleared: tuple[str, ...] | None
    carried: tuple[str, ...] = ()


# Dependencies, and a project pass no manifest describes: today's package-wide
# replace, written to the dependency tier and read the way the bundle serves.
UNBRANCHED_SCOPE = TreeTierBranchScope(stamp=DEPENDENCY_TIER, view=None, cleared=None)


async def tree_tier_scope(
    uow: UnitOfWork, package: Package, manifest: BranchManifest | None
) -> TreeTierBranchScope:
    """The scope for one pass; gated on the PROJECT origin like the branch stamp (R15)."""
    if package.origin is not PackageOrigin.PROJECT or manifest is None:
        return UNBRANCHED_SCOPE
    # '' is cleared too: a pass before #307 wrote the project under '', and
    # readers of ``branch IN (?, '')`` would serve that copy beside this one.
    return TreeTierBranchScope(
        stamp=manifest.name,
        view=manifest.name,
        cleared=(manifest.name, DEPENDENCY_TIER),
        carried=await branches_retired_by(uow, manifest),
    )


def cleared_branches(scope: TreeTierBranchScope) -> tuple[str | None, ...]:
    """Each branch a package-wide delete runs for; ``(None,)`` is every branch."""
    return (None,) if scope.cleared is None else scope.cleared


async def clear_package_members(uow: UnitOfWork, package: str, scope: TreeTierBranchScope) -> None:
    for branch in cleared_branches(scope):
        members_filter = {ModuleMemberFilterField.PACKAGE.value: package}
        if branch is not None:
            members_filter[ModuleMemberFilterField.BRANCH.value] = branch
        await uow.module_members.delete(filter=members_filter)


async def clear_package_trees(uow: UnitOfWork, package: str, scope: TreeTierBranchScope) -> None:
    for branch in cleared_branches(scope):
        await uow.trees.delete_for_package(package, branch=branch)


async def clear_package_references(
    uow: UnitOfWork, package: str, scope: TreeTierBranchScope
) -> None:
    for branch in cleared_branches(scope):
        await uow.references.delete_for_package(package, branch=branch)


def stamp_member_branch(members: Sequence[ModuleMember], branch: str) -> Sequence[ModuleMember]:
    """Members carry their branch in metadata; the row mapper reads it (absent = '')."""
    if branch == DEPENDENCY_TIER:
        return members
    key = ModuleMemberFilterField.BRANCH.value
    return tuple(replace(m, metadata={**m.metadata, key: branch}) for m in members)


async def decisions_to_reconcile(
    uow: UnitOfWork, package: str, scope: TreeTierBranchScope
) -> tuple[DecisionRecord, ...]:
    """The pass's persisted decisions, plus those of the branches it retires.

    Only the working-tree branch carries decision rows (spec §11 O10), so a
    checkout carries them over: reconciled by title, they keep their ids —
    which kept decision chunks still point at through ``chunks.decision_id`` —
    and the upsert re-stamps them with the new branch.
    """
    by_id: dict[int | None, DecisionRecord] = {}
    for branch in (scope.view, *scope.carried):
        for record in await uow.decisions.list_for_package(package, branch=branch):
            by_id[record.id] = record
    return tuple(sorted(by_id.values(), key=lambda r: r.id or 0))


async def replace_node_scores(uow: UnitOfWork, scores: Sequence[NodeScore], branch: str) -> None:
    """Swap the scores one branch reads: its project rows and the dependency tier.

    Project rows are stamped with ``branch``; every other package's rows keep
    the dependency tier (spec §6.1 Q1), which is replaced whole — rows of a
    package no longer indexed go too, as under the pre-branch ``delete_all``.
    The upserts run in compute order, one per consecutive run of a tier, so
    rowids — and every rowid-ordered read — come out as a single upsert would
    give them.
    """
    await uow.node_scores.delete_for_branch(DEPENDENCY_TIER)
    await uow.node_scores.delete_for_package(PROJECT_PACKAGE_NAME, branch=branch)
    for tier, run in groupby(scores, key=lambda s: _score_tier(s, branch)):
        await uow.node_scores.upsert(tuple(run), branch=tier)


async def replace_project_node_scores(
    uow: UnitOfWork, scores: Sequence[NodeScore], branch: str
) -> None:
    """Swap only the project rows of one git-objects branch (#310).

    The dependency tier is every branch's, and :func:`replace_node_scores`
    rewrites it from the pass's graph: run for a branch the bundle does not
    serve, that would re-rank the served branch's dependencies by another
    branch's graph. So the dependency rows the recompute also produced are
    dropped here.
    """
    await uow.node_scores.delete_for_package(PROJECT_PACKAGE_NAME, branch=branch)
    project = tuple(s for s in scores if s.package == PROJECT_PACKAGE_NAME)
    await uow.node_scores.upsert(project, branch=branch)


def _score_tier(score: NodeScore, branch: str) -> str:
    return branch if score.package == PROJECT_PACKAGE_NAME else DEPENDENCY_TIER


async def scored_qname_packages(uow: UnitOfWork, branch: str) -> dict[str, str]:
    """``{qualified_name: package}`` of the chunks one branch's scores cover.

    WHY scoped (#310): once a second branch is indexed the chunk table holds
    every branch's project rows, and the scorer emits one row per key — so an
    unscoped map gave the served branch scores for symbols only the other
    branch has, and the other way round. A stamped branch scores the project
    chunks its membership names; a bundle with no row for ``branch`` (no git,
    the dependency tier) keeps every project chunk. Dependency chunks are every
    branch's. Chunk order is kept, so the scores' rowids come out as before.
    """
    in_branch = await _branch_chunk_ids(uow, branch)
    return {
        qname: package
        for chunk in await uow.chunks.list()
        if (qname := chunk.metadata.get(_QUALIFIED_NAME_KEY))
        and (package := chunk.metadata.get(ChunkFilterField.PACKAGE.value))
        and _scored_on_branch(chunk, package, in_branch)
    }


async def _branch_chunk_ids(uow: UnitOfWork, branch: str) -> frozenset[int] | None:
    """The chunk ids ``branch`` holds; ``None`` when no row tracks it."""
    if await uow.branches.get_branch(branch) is None:
        return None
    return frozenset(m.chunk_id for m in await uow.branch_chunks.list_membership(branch))


def _scored_on_branch(chunk: Chunk, package: str, in_branch: frozenset[int] | None) -> bool:
    return in_branch is None or package != PROJECT_PACKAGE_NAME or chunk.id in in_branch


__all__ = (
    "UNBRANCHED_SCOPE",
    "TreeTierBranchScope",
    "clear_package_members",
    "clear_package_references",
    "clear_package_trees",
    "cleared_branches",
    "decisions_to_reconcile",
    "replace_node_scores",
    "replace_project_node_scores",
    "scored_qname_packages",
    "stamp_member_branch",
    "tree_tier_scope",
)
