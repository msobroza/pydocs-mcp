"""Merge detection for branch retirement (spec §6.8a, amended 2026-09-04; #316).

Three signals, in order: ancestry (the head became reachable from the base
through a merge commit's second parent), the whole-range patch id (a squash
landing — ``is_ancestor`` never fires for one), and a run of per-commit patch
ids over consecutive one-parent steps (a rebase-merge). Every failure mode is
a false negative. Nothing here writes except the landing patch-id cache in
:func:`load_landing_index`; the port is synchronous, so :func:`detect_merges`
runs off the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import groupby

from pydocs_mcp.application.branch_manifest import is_synthetic_branch_name
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.models import LIVE_BRANCH_STATUSES, LandingStep, MergeEvidence
from pydocs_mcp.storage.branch_records import BranchRecord, LandingPatchId
from pydocs_mcp.storage.protocols import UnitOfWork

# One commit is the whole-range squash case: k = 1 classifies SINGLE_COMMIT (§6.8a).
_MIN_REBASE_RUN = 2


@dataclass(frozen=True, slots=True)
class LandingIndex:
    """The lookback's first-parent steps, newest first, each with its patch id."""

    steps: tuple[LandingStep, ...]
    # patch id -> the NEWEST landing carrying it; empty-diff steps are absent.
    by_patch_id: Mapping[str, str]

    @classmethod
    def from_steps(cls, steps: Sequence[LandingStep]) -> LandingIndex:
        by_patch_id: dict[str, str] = {}
        for step in steps:
            if step.patch_id:  # an empty range diff never matches (spec §6.8a)
                by_patch_id.setdefault(step.patch_id, step.sha)
        return cls(tuple(steps), by_patch_id)

    def step(self, sha: str) -> LandingStep | None:
        return next((step for step in self.steps if step.sha == sha), None)


@dataclass(frozen=True, slots=True)
class MergeVerdict:
    """``branch`` landed on the base at ``landing_sha``; ``snapshot`` is the
    ``(pre, post)`` range of a rebase-merge (spec §6.5b ``LINEAR_SNAPSHOT``)."""

    branch: str
    evidence: MergeEvidence
    landing_sha: str
    snapshot: tuple[str, str] | None = None


async def load_landing_index(
    git: GitRepository, uow: UnitOfWork, base: BaseBranch, lookback: int
) -> LandingIndex:
    """The lookback's steps with their patch ids, streaming only uncached landings.

    One diff-less probe names the steps and their parents; the id of a cached
    step comes from ``landing_patch_ids`` (immutable per sha); each contiguous
    run of uncached steps is streamed once, bounded by its own length. The
    first run therefore streams the whole lookback and every later one only
    the new landings (spec §6.2, §6.8a, O16).
    """
    probed = await asyncio.to_thread(git.first_parent_steps, base.tip_sha, max_count=lookback)
    cached = await uow.branches.landing_patch_ids([step.sha for step in probed])
    streamed = await asyncio.to_thread(_stream_uncached_patch_ids, git, probed, cached)
    await uow.branches.upsert_landing_patch_ids(
        [LandingPatchId(sha, patch_id) for sha, patch_id in streamed.items()]
    )
    ids = {**cached, **streamed}
    return LandingIndex.from_steps([replace(s, patch_id=ids.get(s.sha, "")) for s in probed])


def _uncached_runs(
    probed: Sequence[LandingStep], cached: Mapping[str, str]
) -> list[tuple[int, int]]:
    """``(start, length)`` of every contiguous run of steps whose id is not cached."""
    runs: list[tuple[int, int]] = []
    position = 0
    for is_cached, group in groupby(probed, key=lambda step: step.sha in cached):
        length = len(list(group))
        if not is_cached:
            runs.append((position, length))
        position += length
    return runs


def _stream_uncached_patch_ids(
    git: GitRepository, probed: Sequence[LandingStep], cached: Mapping[str, str]
) -> dict[str, str]:
    # A run's newest step starts its own first-parent walk, so the stream never
    # re-hashes a cached landing — not even when a longer lookback leaves the
    # uncached steps at the tail. An empty-diff step is cached as "" too.
    ids: dict[str, str] = {}
    for start, length in _uncached_runs(probed, cached):
        landed = git.first_parent_landings(probed[start].sha, max_count=length)
        ids.update((step.sha, step.patch_id) for step in landed)
    return ids


def merge_candidates(
    records: Sequence[BranchRecord],
    *,
    protected: Collection[str],
    base_name: str | None = None,
) -> tuple[BranchRecord, ...]:
    """The rows detection examines (spec §6.8a and the #316 safety cases).

    Every live branch row, whether or not its local ref still exists — never a
    landing unit, the base itself (trivially an ancestor of its own tip), a
    ``protected`` (checked out) name, the non-git sentinel, or a detached row.
    Pinned rows ARE examined: their evidence is stamped, they just never
    transition.
    """
    return tuple(r for r in records if _is_merge_candidate(r, protected, base_name))


def is_live_branch_row(record: BranchRecord) -> bool:
    """A branch row the lifecycle still moves: ``ACTIVE`` or ``INACTIVE``, never
    a landing unit, the non-git sentinel or a detached row (#316 safety d, e).
    A retired row is never examined again, and landing units live outside the
    lifecycle (§6.5b)."""
    if record.is_landing_unit or record.status not in LIVE_BRANCH_STATUSES:
        return False
    return not is_synthetic_branch_name(record.name)


def _is_merge_candidate(
    record: BranchRecord, protected: Collection[str], base_name: str | None
) -> bool:
    if not is_live_branch_row(record):
        return False
    return record.name != base_name and record.name not in protected


def _detection_head(
    git: GitRepository, record: BranchRecord, local_heads: Mapping[str, str]
) -> str | None:
    """The live head; for a row whose ref is gone, its stored head while the
    object survives.

    #316: a squash-merged branch is usually deleted before the next pass, and
    only detection at its stored head keeps it MERGED with its landing unit
    (spec §6.8a scans every live row; §6.5b). ``head_sha`` probes without
    raising, so a gc'd head skips one row — it retires as DELETED — instead of
    failing every run. A branch that moved after the last pass is judged at the
    older head: a stated false negative.
    """
    live = local_heads.get(record.name)
    if live is not None:
        return live
    return git.head_sha(record.head_sha) if record.head_sha else None


def detect_merges(
    git: GitRepository,
    base: BaseBranch,
    records: Sequence[BranchRecord],
    index: LandingIndex,
    *,
    local_heads: Mapping[str, str],
    protected: Collection[str] = (),
) -> tuple[MergeVerdict, ...]:
    """One verdict per candidate that landed on ``base``, judged at its live
    head, or at its stored head when its ref is gone.

    Synchronous and git-bound: call it through ``asyncio.to_thread``.
    """
    verdicts = (
        _detect_row(git, base, row, index, local_heads)
        for row in merge_candidates(records, protected=protected, base_name=base.name)
    )
    return tuple(verdict for verdict in verdicts if verdict is not None)


def _detect_row(
    git: GitRepository,
    base: BaseBranch,
    record: BranchRecord,
    index: LandingIndex,
    local_heads: Mapping[str, str],
) -> MergeVerdict | None:
    head = _detection_head(git, record, local_heads)
    return None if head is None else _detect_one(git, base, record.name, head, index)


def _detect_one(
    git: GitRepository, base: BaseBranch, name: str, head: str, index: LandingIndex
) -> MergeVerdict | None:
    if git.is_ancestor(head, base.tip_sha):
        landing = _ancestor_landing(git, head, index.steps)
        return None if landing is None else MergeVerdict(name, MergeEvidence.ANCESTOR, landing)
    # Recomputed at detection time, never the stamped one: a branch that
    # merged the base into itself moved it (spec §6.8a).
    merge_base = git.merge_base(base.tip_sha, head)
    if not merge_base:  # no common ancestor: detection is skipped (spec §6.5)
        return None
    return _patch_id_verdict(git, name, merge_base, head, index)


def _first_parent_chain(steps: Sequence[LandingStep]) -> list[str]:
    """The steps' shas newest first, plus the oldest step's first parent."""
    chain = [step.sha for step in steps]
    if steps and steps[-1].parent_shas:
        chain.append(steps[-1].parent_shas[0])
    return chain


def _ancestor_landing(git: GitRepository, head: str, steps: Sequence[LandingStep]) -> str | None:
    """The first-parent step that made ``head`` reachable, or ``None``.

    ``head`` is an ancestor of the tip (``steps[0]``) and reachability is
    monotone along the first-parent chain, so a binary search finds the step
    ``S`` with ``head`` reachable from ``S`` but not from ``S^1``: the merge
    whose second parent carried it. A head ON the chain has no commits of its
    own — a branch cut from the base, or fast-forwarded into it (#316 safety
    c) — and a head reachable from the chain's oldest end landed before the
    lookback (a stated false negative, spec §6.8a).
    """
    chain = _first_parent_chain(steps)
    if not chain or head in chain or git.is_ancestor(head, chain[-1]):
        return None
    newer, older = 0, len(chain) - 1  # head reaches chain[newer], never chain[older]
    while older - newer > 1:
        middle = (newer + older) // 2
        if git.is_ancestor(head, chain[middle]):
            newer = middle
        else:
            older = middle
    return chain[newer]


def _patch_id_verdict(
    git: GitRepository, name: str, merge_base: str, head: str, index: LandingIndex
) -> MergeVerdict | None:
    whole = git.patch_id(merge_base, head)
    if not whole:  # an empty range diff never matches (spec §6.8a)
        return None
    if whole in index.by_patch_id:
        return MergeVerdict(name, MergeEvidence.PATCH_ID_MATCH, index.by_patch_id[whole])
    per_commit = [patch_id for _, patch_id in git.patch_ids_per_commit(merge_base, head)]
    snapshot = _rebase_run(per_commit, index.steps)
    if snapshot is None:
        return None
    return MergeVerdict(name, MergeEvidence.REBASE_PATCH_ID_MATCH, snapshot[1], snapshot=snapshot)


def _rebase_run(wanted: Sequence[str], steps: Sequence[LandingStep]) -> tuple[str, str] | None:
    """``(pre, post)`` of the newest run of consecutive one-parent steps whose
    ids are ``wanted`` in order (oldest first): ``pre`` is the oldest matched
    step's first parent, ``post`` the newest matched step (spec §6.5b)."""
    if len(wanted) < _MIN_REBASE_RUN:
        return None
    oldest_first = list(reversed(steps))
    for start in reversed(range(len(oldest_first) - len(wanted) + 1)):
        window = oldest_first[start : start + len(wanted)]
        if _is_linear_run(window, wanted):
            return window[0].parent_shas[0], window[-1].sha
    return None


def _is_linear_run(window: Sequence[LandingStep], wanted: Sequence[str]) -> bool:
    # Consecutive in the UNFILTERED line: a merge step inside the run breaks it,
    # or the snapshot range would silently include the merge's changes.
    pairs = zip(window, wanted, strict=True)
    return all(len(step.parent_shas) == 1 and step.patch_id == pid for step, pid in pairs)


__all__ = (
    "LandingIndex",
    "MergeVerdict",
    "detect_merges",
    "is_live_branch_row",
    "load_landing_index",
    "merge_candidates",
)
