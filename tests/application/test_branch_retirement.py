"""Transitions, the landing-unit link, the grace purge and the operator verbs
(spec §6.8a, §6.5b; #316) over the in-memory stores."""

from __future__ import annotations

import pytest

from pydocs_mcp.application.branch_retirement import (
    BranchVerb,
    BranchVerbError,
    RetirementPolicy,
    apply_branch_verb,
    apply_merge_verdicts,
    purge_due,
    retire_deleted,
    retired_branch_message,
)
from pydocs_mcp.application.merge_detection import LandingIndex, MergeVerdict
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    Chunk,
    LandingKind,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.retrieval.config.git_models import BranchRetentionConfig
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from tests._fakes import make_fake_uow_factory

SQUASH, FEATURE, MB, MERGE = "3" * 40, "4" * 40, "2" * 40, "5" * 40
DAY = 86400.0
POLICY = RetirementPolicy(grace_days=7, auto_retire_merged=True, auto_retire_deleted=True)
_INDEX = LandingIndex.from_steps(
    (
        LandingStep(MERGE, (SQUASH, FEATURE), 20.0, "merge feature/m", "pid-m"),
        LandingStep(SQUASH, (MB,), 10.0, "feature (#1)", "pid"),
    )
)


def _row(name: str, **overrides: object) -> BranchRecord:
    return BranchRecord(name, FEATURE, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **overrides)


def _verdict(
    branch: str,
    landing: str = SQUASH,
    *,
    evidence: MergeEvidence = MergeEvidence.PATCH_ID_MATCH,
    snapshot: tuple[str, str] | None = None,
) -> MergeVerdict:
    return MergeVerdict(branch, evidence, landing, snapshot)


async def test_merged_transition_links_the_landing_unit_and_copies_the_diff_slice() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("feature/x"))
        await uow.branch_chunks.replace_membership(
            "feature/x",
            [
                ChunkMembership("feature/x", 1, "a.py", 1, 2),
                ChunkMembership("feature/x", 2, "a.py", 3, 4, slice=BranchSlice.DIFF),
            ],
        )
        names = await apply_merge_verdicts(
            uow, [_verdict("feature/x")], base_name="main", now=100.0, policy=POLICY, index=_INDEX
        )
        branch = await uow.branches.get_branch("feature/x")
        unit = await uow.branches.get_branch(SQUASH)
        unit_rows = await uow.branch_chunks.list_membership(SQUASH)
    assert names == ("feature/x",)
    assert branch.status is BranchStatus.MERGED
    # O18: merged_into keeps the base NAME, landing_sha carries the sha.
    assert branch.merged_into == "main" and branch.landing_sha == SQUASH
    assert branch.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert branch.retired_at == 100.0 and branch.purge_after == 100.0 + 7 * DAY
    assert unit.is_landing_unit and unit.landing_kind is LandingKind.SINGLE_COMMIT
    assert (unit.merge_base_sha, unit.head_sha, unit.landed_at) == (MB, SQUASH, 10.0)
    assert unit.worktree_path is None and unit.source is BranchIndexSource.GIT_OBJECTS
    assert unit.status is BranchStatus.ACTIVE and unit.base_name == "main"
    assert [m.chunk_id for m in unit_rows] == [2]  # the DIFF slice only (§6.5b)


async def test_the_unit_kind_follows_the_landing_shape() -> None:
    factory = make_fake_uow_factory()
    rebase = _verdict(
        "rebased", SQUASH, evidence=MergeEvidence.REBASE_PATCH_ID_MATCH, snapshot=(MB, SQUASH)
    )
    merge = _verdict("merged", MERGE, evidence=MergeEvidence.ANCESTOR)
    async with factory() as uow:
        for name in ("rebased", "merged"):
            await uow.branches.upsert_branch(_row(name))
        await apply_merge_verdicts(
            uow, [rebase, merge], base_name="main", now=1.0, policy=POLICY, index=_INDEX
        )
        snapshot = await uow.branches.get_branch(SQUASH)
        merge_unit = await uow.branches.get_branch(MERGE)
    assert snapshot.landing_kind is LandingKind.LINEAR_SNAPSHOT and snapshot.merge_base_sha == MB
    assert merge_unit.landing_kind is LandingKind.MERGE_COMMIT
    assert merge_unit.merge_base_sha == SQUASH  # the landing's first parent


async def test_pinned_rows_and_a_disabled_policy_only_stamp_the_evidence() -> None:
    factory = make_fake_uow_factory()
    keep = RetirementPolicy(grace_days=7, auto_retire_merged=False, auto_retire_deleted=True)
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("pinned", pinned=True))
        await uow.branches.upsert_branch(_row("plain"))
        transitioned = await apply_merge_verdicts(
            uow,
            [_verdict("pinned"), _verdict("plain")],
            base_name="main",
            now=1.0,
            policy=keep,
            index=_INDEX,
        )
        rows = [await uow.branches.get_branch(n) for n in ("pinned", "plain")]
        unit = await uow.branches.get_branch(SQUASH)
    assert transitioned == ()
    for row in rows:
        assert row.status is BranchStatus.ACTIVE and row.purge_after is None
        assert row.landing_sha == SQUASH and row.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert unit is None  # no transition, no unit


async def test_an_existing_row_under_the_landing_sha_is_never_overwritten() -> None:
    factory = make_fake_uow_factory()
    pinned_unit = _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT, pinned=True, landed_at=9.0)
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("feature/x"))
        await uow.branches.upsert_branch(pinned_unit)
        await apply_merge_verdicts(
            uow, [_verdict("feature/x")], base_name="main", now=1.0, policy=POLICY, index=_INDEX
        )
        assert await uow.branches.get_branch(SQUASH) == pinned_unit
    # A BRANCH literally named like the sha keeps its row and receives no copy.
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("feature/x"))
        await uow.branch_chunks.replace_membership(
            "feature/x", [ChunkMembership("feature/x", 7, "a.py", slice=BranchSlice.DIFF)]
        )
        await uow.branches.upsert_branch(_row(SQUASH))
        await apply_merge_verdicts(
            uow, [_verdict("feature/x")], base_name="main", now=1.0, policy=POLICY, index=_INDEX
        )
        assert await uow.branches.get_branch(SQUASH) == _row(SQUASH)
        assert await uow.branch_chunks.count_for_branch(SQUASH) == 0


# ── Deleted refs ──


async def _seed(uow, *records: BranchRecord) -> None:
    for record in records:
        await uow.branches.upsert_branch(record)


async def test_deleted_refs_retire_every_live_row_outside_the_lifecycle_exemptions() -> None:
    """#316 safety (b), (d), (e): protected, pinned, landing units, the non-git
    sentinel and detached rows never retire as deleted."""
    factory = make_fake_uow_factory()
    exempt = [
        _row("checked-out"),
        _row("pinned", pinned=True),
        _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT),
        _row(NON_GIT_BRANCH_NAME),
        _row("detached-1234567"),
        _row("already", status=BranchStatus.MERGED),
    ]
    async with factory() as uow:
        await _seed(uow, _row("main"), _row("gone"), _row("idle", status=BranchStatus.INACTIVE))
        await _seed(uow, *exempt)
        retired = await retire_deleted(
            uow, ["main"], now=10.0, policy=POLICY, protected={"checked-out"}
        )
        gone = await uow.branches.get_branch("gone")
        untouched = [await uow.branches.get_branch(r.name) for r in exempt]
    assert retired == ("gone", "idle")
    assert gone.status is BranchStatus.DELETED
    assert gone.retired_at == 10.0 and gone.purge_after == 10.0 + 7 * DAY
    assert gone.merged_into is None
    assert untouched == exempt


async def test_auto_retire_deleted_off_retires_nothing() -> None:
    factory = make_fake_uow_factory()
    off = RetirementPolicy(grace_days=7, auto_retire_merged=True, auto_retire_deleted=False)
    async with factory() as uow:
        await _seed(uow, _row("gone"))
        assert await retire_deleted(uow, [], now=1.0, policy=off) == ()
        assert (await uow.branches.get_branch("gone")).status is BranchStatus.ACTIVE


# ── The grace purge ──


async def _seed_owned(uow, name: str, **overrides: object) -> None:
    await uow.branches.upsert_branch(_row(name, **overrides))
    (chunk_id,) = await uow.chunks.insert_returning_ids(
        (Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title=name, text=name),)
    )
    await uow.branch_chunks.replace_membership(name, [ChunkMembership(name, chunk_id, "a.py")])
    await uow.branches.replace_files(name, [BranchFile(name, "a.py", "blob")])


async def test_rows_survive_the_grace_window_and_vanish_after_it() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed_owned(uow, "gone")
        await retire_deleted(uow, [], now=10.0, policy=POLICY)
        assert await purge_due(uow, now=10.0 + 6 * DAY) == ()
        assert await uow.branch_chunks.count_for_branch("gone") == 1
        assert await purge_due(uow, now=10.0 + 7 * DAY) == ("gone",)
        assert await uow.branch_chunks.count_for_branch("gone") == 0
        assert await uow.branches.count_files("gone") == 0
        assert await uow.chunks.list(filter={"package": PROJECT_PACKAGE_NAME}) == []
        # The record stays as the tombstone, and a purged row is not purged twice.
        assert (await uow.branches.get_branch("gone")).status is BranchStatus.DELETED
        assert await purge_due(uow, now=10.0 + 8 * DAY) == ()


async def test_the_purge_spares_pinned_protected_live_rows_and_landing_units() -> None:
    factory = make_fake_uow_factory()
    due = {"retired_at": 1.0, "purge_after": 2.0}
    async with factory() as uow:
        await _seed_owned(uow, "pinned", status=BranchStatus.DELETED, pinned=True, **due)
        await _seed_owned(uow, "checked-out", status=BranchStatus.DELETED, **due)
        await _seed_owned(uow, "live", **due)
        # A collected unit is INACTIVE (spec §6.5b) — a purgeable status for a
        # branch row, so only the landing-unit guard spares it.
        unit = {"landing_kind": LandingKind.SINGLE_COMMIT, "status": BranchStatus.INACTIVE}
        await _seed_owned(uow, SQUASH, **unit, **due)
        await _seed_owned(uow, "manual", status=BranchStatus.INACTIVE, **due)
        await _seed_owned(uow, "merged", status=BranchStatus.MERGED, **due)
        purged = await purge_due(uow, now=3.0, protected={"checked-out"})
        unit_rows = await uow.branch_chunks.count_for_branch(SQUASH)
    assert purged == ("manual", "merged")
    assert unit_rows == 1


# ── Operator verbs ──


async def test_retire_moves_an_active_row_inactive_with_a_purge_date() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed(uow, _row("main", is_default=True), _row("feature/x"))
        await apply_branch_verb(uow, BranchVerb.RETIRE, "feature/x", now=5.0, policy=POLICY)
        row = await uow.branches.get_branch("feature/x")
    assert row.status is BranchStatus.INACTIVE
    assert (row.retired_at, row.purge_after) == (5.0, 5.0 + 7 * DAY)


async def test_purge_drops_the_rows_at_once_and_keeps_the_tombstone() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed(uow, _row("main", is_default=True))
        await _seed_owned(uow, "feature/x")
        freed = await apply_branch_verb(uow, BranchVerb.PURGE, "feature/x", now=5.0, policy=POLICY)
        row = await uow.branches.get_branch("feature/x")
        assert await uow.branch_chunks.count_for_branch("feature/x") == 0
    assert len(freed) == 1
    # A purged row has no index left to serve: it is no longer ACTIVE.
    assert row.status is BranchStatus.INACTIVE and row.purge_after == 5.0


async def test_pin_and_unpin_accept_branches_and_landing_units() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed(uow, _row("feature/x"), _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT))
        await apply_branch_verb(uow, BranchVerb.PIN, "feature/x", now=1.0, policy=POLICY)
        await apply_branch_verb(uow, BranchVerb.PIN, SQUASH, now=1.0, policy=POLICY)
        assert (await uow.branches.get_branch("feature/x")).pinned is True
        assert (await uow.branches.get_branch(SQUASH)).pinned is True
        await apply_branch_verb(uow, BranchVerb.UNPIN, "feature/x", now=1.0, policy=POLICY)
        assert (await uow.branches.get_branch("feature/x")).pinned is False


async def test_an_unknown_name_is_refused_with_the_indexed_branches() -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed(
            uow,
            _row("main"),
            _row("feature/x"),
            _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT),
        )
        with pytest.raises(BranchVerbError) as info:
            await apply_branch_verb(uow, BranchVerb.RETIRE, "nope", now=1.0, policy=POLICY)
    assert isinstance(info.value, KeyError)  # the plan's contract: unknown names are KeyErrors
    assert str(info.value) == "no indexed branch 'nope'; indexed: feature/x, main"


@pytest.mark.parametrize("verb", [BranchVerb.RETIRE, BranchVerb.PURGE])
async def test_retire_and_purge_refuse_the_served_branch_and_landing_units(
    verb: BranchVerb,
) -> None:
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await _seed(
            uow, _row("main", is_default=True), _row(SQUASH, landing_kind=LandingKind.SINGLE_COMMIT)
        )
        with pytest.raises(BranchVerbError, match="checked-out branch 'main'"):
            await apply_branch_verb(uow, verb, "main", now=1.0, policy=POLICY)
        with pytest.raises(BranchVerbError, match="landing unit"):
            await apply_branch_verb(uow, verb, SQUASH, now=1.0, policy=POLICY)
        assert (await uow.branches.get_branch("main")).status is BranchStatus.ACTIVE


def test_the_policy_is_built_from_the_retention_config() -> None:
    config = BranchRetentionConfig(grace_days=3, auto_retire_merged=False)
    assert RetirementPolicy.from_config(config) == RetirementPolicy(3, False, True)


# ── The retired-branch message (consumed by the selector resolution) ──


def test_the_merged_message_names_the_base_the_landing_and_the_way_back() -> None:
    merged = _row(
        "feature/x",
        status=BranchStatus.MERGED,
        merged_into="main",
        landing_sha="3e1a9c2" + "0" * 33,
        retired_at=1_788_220_800.0,  # 2026-09-01 UTC
    )
    assert retired_branch_message(merged) == (
        "branch 'feature/x' was merged into main at 3e1a9c2 (2026-09-01); its index was "
        "retired. Search main, or run: pydocs-mcp index . --branch feature/x"
    )


def test_the_deleted_message_says_how_to_bring_the_index_back() -> None:
    deleted = _row("gone", status=BranchStatus.DELETED, retired_at=1_788_220_800.0)
    assert retired_branch_message(deleted) == (
        "branch 'gone' was deleted locally (2026-09-01); its index was retired. "
        "Run: pydocs-mcp index . --branch gone after recreating it"
    )
