"""Squash, ancestor and rebase-merge detection over the fake port (spec §6.8a, #316).

The fake answers ``is_ancestor`` from an explicit ``ancestry`` set, so each test
states the commit graph it relies on. Real repositories: see
``tests/integration/test_merge_detection_git.py``.
"""

from __future__ import annotations

from dataclasses import replace

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.merge_detection import (
    LandingIndex,
    detect_merges,
    load_landing_index,
    merge_candidates,
)
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    BranchStatus,
    LandingKind,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository, make_fake_uow_factory

# The base's first-parent line, newest first: TIP ← P1 ← P0 ← ROOT.
TIP, P1, P0, ROOT = "a" * 40, "b" * 40, "c" * 40, "d" * 40
FEATURE, MB, SQUASH = "e" * 40, "f" * 40, "1" * 40
R1, R2, R3 = "2" * 40, "3" * 40, "4" * 40
BASE = BaseBranch("main", TIP, None)


def _row(name: str, **overrides: object) -> BranchRecord:
    return BranchRecord(name, "0" * 40, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **overrides)


def _step(sha: str, *parents: str, patch_id: str = "", landed_at: float = 1.0) -> LandingStep:
    return LandingStep(sha, parents, landed_at, f"subject {sha[:4]}", patch_id)


def _merge_line() -> tuple[LandingStep, ...]:
    """``TIP`` merges ``FEATURE`` as its second parent onto ``P1``."""
    return (_step(TIP, P1, FEATURE), _step(P1, P0), _step(P0, ROOT))


def _merged_feature_git() -> FakeGitRepository:
    # FEATURE is reachable from TIP only; P1 and P0 are on the first-parent line
    # (git counts a commit as its own ancestor).
    ancestry = {(FEATURE, TIP), (P1, TIP), (P0, TIP), (P0, P1), (TIP, TIP)}
    return FakeGitRepository(ancestry=ancestry)


def _squash_landed_git(**extra: object) -> FakeGitRepository:
    return FakeGitRepository(
        merge_bases={frozenset((TIP, FEATURE)): MB}, patch_ids={(MB, FEATURE): "pid-f"}, **extra
    )


_SQUASH_INDEX = LandingIndex.from_steps((_step(TIP, SQUASH), _step(SQUASH, MB, patch_id="pid-f")))


def test_squash_landing_is_detected_by_patch_id_while_ancestry_is_false() -> None:
    rows = [_row("feature/x")]
    heads = {"feature/x": FEATURE}
    (verdict,) = detect_merges(_squash_landed_git(), BASE, rows, _SQUASH_INDEX, local_heads=heads)
    assert verdict.branch == "feature/x" and verdict.landing_sha == SQUASH
    assert verdict.evidence is MergeEvidence.PATCH_ID_MATCH and verdict.snapshot is None


def test_ancestor_landing_names_the_merge_commit_that_carried_the_head() -> None:
    index = LandingIndex.from_steps(_merge_line())
    rows = [_row("feature/x")]
    heads = {"feature/x": FEATURE}
    (verdict,) = detect_merges(_merged_feature_git(), BASE, rows, index, local_heads=heads)
    assert verdict.evidence is MergeEvidence.ANCESTOR and verdict.landing_sha == TIP


def test_a_branch_on_the_first_parent_line_has_no_commits_of_its_own() -> None:
    """#316 safety (c): a branch cut from the base (or fast-forwarded into it)
    is an ancestor of the tip, but nothing of its own ever landed."""
    index = LandingIndex.from_steps(_merge_line())
    rows = [_row("fresh"), _row("at-tip")]
    heads = {"fresh": P1, "at-tip": TIP}
    assert detect_merges(_merged_feature_git(), BASE, rows, index, local_heads=heads) == ()


def test_an_ancestor_that_landed_before_the_lookback_is_not_claimed() -> None:
    # Reachable from the oldest step's parent: the landing is outside the window.
    git = FakeGitRepository(
        ancestry={(FEATURE, TIP), (FEATURE, P1), (FEATURE, P0), (FEATURE, ROOT)}
    )
    index = LandingIndex.from_steps(_merge_line())
    heads = {"feature/x": FEATURE}
    assert detect_merges(git, BASE, [_row("feature/x")], index, local_heads=heads) == ()


def test_the_base_branch_itself_is_never_marked_merged() -> None:
    """#316 safety (a): the base is trivially an ancestor of its own tip."""
    index = LandingIndex.from_steps(_merge_line())
    # Even a base row whose live head would read as merged is never examined.
    heads = {"main": FEATURE}
    assert (
        detect_merges(_merged_feature_git(), BASE, [_row("main")], index, local_heads=heads) == ()
    )


def test_a_checked_out_branch_is_never_examined() -> None:
    """#316 safety (b): a protected (checked-out) name is skipped like lru_evictions does."""
    index = LandingIndex.from_steps(_merge_line())
    heads = {"feature/x": FEATURE}
    verdicts = detect_merges(
        _merged_feature_git(),
        BASE,
        [_row("feature/x")],
        index,
        local_heads=heads,
        protected={"feature/x"},
    )
    assert verdicts == ()


def _rebase_git(per_commit: tuple[tuple[str, str], ...]) -> FakeGitRepository:
    return FakeGitRepository(
        merge_bases={frozenset((TIP, FEATURE)): MB},
        patch_ids={(MB, FEATURE): "whole"},
        commit_patch_ids={(MB, FEATURE): per_commit},
    )


_THREE_COMMITS = (("c1", "p1"), ("c2", "p2"), ("c3", "p3"))


def test_rebase_merge_is_detected_by_a_run_of_per_commit_patch_ids() -> None:
    steps = (
        _step(TIP, R3, patch_id="p-later"),
        _step(R3, R2, patch_id="p3"),
        _step(R2, R1, patch_id="p2"),
        _step(R1, MB, patch_id="p1"),
        _step(MB, ROOT, patch_id="p-mb"),
    )
    heads = {"feature/x": FEATURE}
    rows = [_row("feature/x")]
    git = _rebase_git(_THREE_COMMITS)
    (verdict,) = detect_merges(git, BASE, rows, LandingIndex.from_steps(steps), local_heads=heads)
    assert verdict.evidence is MergeEvidence.REBASE_PATCH_ID_MATCH
    assert verdict.landing_sha == R3 and verdict.snapshot == (MB, R3)


def test_a_rebase_run_must_be_consecutive_one_parent_steps() -> None:
    # The middle id lands on a merge commit: the run is broken (spec §6.8a).
    steps = (
        _step(TIP, R3, patch_id="p3"),
        _step(R3, R1, "9" * 40, patch_id="p2"),
        _step(R1, MB, patch_id="p1"),
    )
    heads = {"feature/x": FEATURE}
    rows = [_row("feature/x")]
    git = _rebase_git(_THREE_COMMITS)
    assert detect_merges(git, BASE, rows, LandingIndex.from_steps(steps), local_heads=heads) == ()


def test_failure_modes_are_false_negatives() -> None:
    heads = {"feature/x": FEATURE}
    rows = [_row("feature/x")]
    # The branch diff differs from every landing, and one commit cannot form a run.
    differs = _rebase_git((("c1", "x"),))
    assert detect_merges(differs, BASE, rows, _SQUASH_INDEX, local_heads=heads) == ()
    # An empty range diff never matches, even against an empty-diff landing.
    empty = FakeGitRepository(merge_bases={frozenset((TIP, FEATURE)): MB})
    assert detect_merges(empty, BASE, rows, _SQUASH_INDEX, local_heads=heads) == ()
    # No common ancestor with the base: detection is skipped (spec §6.5).
    assert detect_merges(FakeGitRepository(), BASE, rows, _SQUASH_INDEX, local_heads=heads) == ()


def test_a_row_whose_ref_is_gone_is_judged_at_its_stored_head() -> None:
    """#316: the usual flow squash-merges a branch, then deletes it before the
    next pass. Spec §6.8a scans every live row, so the stored head still
    decides MERGED — the landing unit keeps its diff (§6.5b, ADR 0024)."""
    rows = [replace(_row("feature/x"), head_sha=FEATURE)]
    git = _squash_landed_git(objects={FEATURE})
    (verdict,) = detect_merges(git, BASE, rows, _SQUASH_INDEX, local_heads={})
    assert (verdict.branch, verdict.landing_sha) == ("feature/x", SQUASH)
    assert verdict.evidence is MergeEvidence.PATCH_ID_MATCH


def test_a_row_whose_ref_and_head_object_are_gone_is_skipped() -> None:
    """A gc'd head is one skipped row (it retires as DELETED), never a git
    error that would fail every run."""
    rows = [replace(_row("feature/x"), head_sha=FEATURE)]
    assert detect_merges(_squash_landed_git(), BASE, rows, _SQUASH_INDEX, local_heads={}) == ()


def test_rows_outside_the_branch_lifecycle_are_never_examined() -> None:
    """#316 safety (d)/(e): landing units, retired rows, the non-git sentinel
    and detached rows spawn no git call at all."""
    unit = _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT)
    rows = [
        unit,
        _row("gone-status", status=BranchStatus.DELETED),
        _row("merged", status=BranchStatus.MERGED),
        _row(NON_GIT_BRANCH_NAME),
        _row("detached-1234567"),
    ]
    heads = {SQUASH: FEATURE, "gone-status": FEATURE, "merged": FEATURE}
    heads |= {NON_GIT_BRANCH_NAME: FEATURE, "detached-1234567": FEATURE}
    failing = FakeGitRepository(fail=True)  # any git call would raise
    index = LandingIndex.from_steps(_merge_line())
    assert detect_merges(failing, BASE, rows, index, local_heads=heads) == ()


def test_pinned_inactive_and_ref_gone_rows_are_candidates() -> None:
    rows = [
        _row("pinned", pinned=True),
        _row("idle", status=BranchStatus.INACTIVE),
        _row("ref-gone"),
    ]
    names = [r.name for r in merge_candidates(rows, protected=())]
    assert names == ["pinned", "idle", "ref-gone"]


# ── The landing index: stream only the landings whose id is not cached ──


def _landing_line() -> tuple[LandingStep, ...]:
    return (
        _step(TIP, P1, patch_id="pt", landed_at=3.0),
        _step(P1, P0, FEATURE, patch_id="p1", landed_at=2.0),
        _step(P0, ROOT, patch_id="p0", landed_at=1.0),
    )


async def test_the_first_load_streams_the_whole_lookback_once() -> None:
    git = FakeGitRepository(landings=_landing_line())
    factory = make_fake_uow_factory()
    async with factory() as uow:
        index = await load_landing_index(git, uow, BASE, lookback=10)
        cached = await uow.branches.landing_patch_ids([TIP, P1, P0])
    assert git.landing_calls == [(TIP, 3, None)]
    assert index.by_patch_id == {"pt": TIP, "p1": P1, "p0": P0}
    assert index.steps == _landing_line()
    assert cached == {TIP: "pt", P1: "p1", P0: "p0"}


async def test_a_second_load_spawns_no_patch_id_work_for_cached_landings() -> None:
    git = FakeGitRepository(landings=_landing_line())
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await load_landing_index(git, uow, BASE, lookback=10)
    async with factory() as uow:
        again = await load_landing_index(git, uow, BASE, lookback=10)
    assert git.landing_calls == [(TIP, 3, None)]  # the probe alone ran the second time
    assert len(git.step_probe_calls) == 2
    # Cached steps keep their full metadata: parents and commit time come from the probe.
    assert again.steps == _landing_line()


async def test_a_new_landing_streams_only_itself() -> None:
    git = FakeGitRepository(landings=_landing_line())
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await load_landing_index(git, uow, BASE, lookback=10)
    newest = _step("5" * 40, TIP, patch_id="pn", landed_at=4.0)
    git.landings = (newest, *git.landings)
    moved = BaseBranch("main", newest.sha, None)
    async with factory() as uow:
        index = await load_landing_index(git, uow, moved, lookback=10)
    assert git.landing_calls[-1] == (newest.sha, 1, None)
    assert index.by_patch_id == {"pn": newest.sha, "pt": TIP, "p1": P1, "p0": P0}


async def test_a_longer_lookback_streams_only_the_uncached_tail() -> None:
    git = FakeGitRepository(landings=_landing_line())
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await load_landing_index(git, uow, BASE, lookback=2)
    async with factory() as uow:
        index = await load_landing_index(git, uow, BASE, lookback=3)
    assert git.landing_calls == [(TIP, 2, None), (P0, 1, None)]
    assert index.by_patch_id["p0"] == P0


async def test_empty_diff_landings_are_cached_but_never_matched() -> None:
    steps = (
        _step(TIP, P1, patch_id=""),
        _step(P1, P0, patch_id="dup"),
        _step(P0, ROOT, patch_id="dup"),
    )
    git = FakeGitRepository(landings=steps)
    factory = make_fake_uow_factory()
    async with factory() as uow:
        index = await load_landing_index(git, uow, BASE, lookback=10)
    async with factory() as uow:
        await load_landing_index(git, uow, BASE, lookback=10)
    assert git.landing_calls == [(TIP, 3, None)]  # the empty id is cached too
    assert index.by_patch_id == {"dup": P1}  # the newest landing of an id wins
