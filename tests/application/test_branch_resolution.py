"""Selector resolution (spec §6.4, §6.5b, §6.11; #311)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_mcp.application.branch_directory import BranchDirectory, BranchSnapshot
from pydocs_mcp.application.branch_resolution import (
    NULL_RESOLUTION,
    BranchSelectorKind,
    landing_unit_error,
    landing_unit_suggestion,
    resolve_branch_selector,
)
from pydocs_mcp.application.branch_retirement import RetirementPolicy, apply_merge_verdicts
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.merge_detection import LandingIndex, MergeVerdict
from pydocs_mcp.application.upstream_status import UpstreamStatus
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    BranchStatus,
    LandingKind,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import make_fake_uow_factory

A, B, U1, U2 = "a" * 40, "b" * 40, "1234567" + "0" * 33, "1234567" + "1" * 33


def _row(name: str, head: str = A, **kw: object) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _unit(sha: str, kind: LandingKind = LandingKind.SINGLE_COMMIT, **kw: object) -> BranchRecord:
    return BranchRecord(
        sha, sha, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, landing_kind=kind, **kw
    )


def _snap(
    *records: BranchRecord, live: str | None = "main", heads: dict[str, str] | None = None
) -> BranchSnapshot:
    default = next((r.name for r in records if r.is_default), None)
    return BranchSnapshot(records, default, live, heads or {})


def test_empty_selector_prefers_the_live_branch_when_it_has_a_live_row() -> None:
    snap = _snap(
        _row("main", is_default=True), _row("feature/x"), live="feature/x", heads={"feature/x": B}
    )
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "feature/x" and resolved.kind is BranchSelectorKind.DEFAULT
    assert resolved.live_head == B and resolved.index_stale is True
    assert resolved.suggestion is None


def test_the_resolution_carries_its_branch_upstream_status_and_no_other() -> None:
    """#318: the behind-upstream facts the lane published ride the resolution
    to the envelope — the resolved branch's only."""
    mine = UpstreamStatus("feature/x", "origin/feature/x", 0, 3, None)
    other = UpstreamStatus("main", "origin/main", 0, 1, None)
    snap = replace(
        _snap(_row("main", is_default=True), _row("feature/x"), live="feature/x"),
        upstream={"feature/x": mine, "main": other},
    )
    assert resolve_branch_selector("", snap).upstream == mine
    assert resolve_branch_selector("main", snap).upstream == other
    assert resolve_branch_selector("", _snap(_row("main", is_default=True))).upstream is None


def test_an_inactive_live_row_still_answers_the_empty_selector() -> None:
    snap = _snap(
        _row("main", is_default=True),
        _row("feature/x", status=BranchStatus.INACTIVE),
        live="feature/x",
    )
    assert resolve_branch_selector("", snap).name == "feature/x"


def test_empty_selector_falls_back_to_the_default_row_with_the_index_suggestion() -> None:
    snap = _snap(_row("main", is_default=True), live="feature/y", heads={"main": A})
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "main" and resolved.kind is BranchSelectorKind.DEFAULT
    assert resolved.index_stale is False
    # Contract §2.3: every suggestion value carries the deterministic prefix.
    assert resolved.suggestion == (
        "[suggestion: checked-out branch 'feature/y' is not indexed; "
        "run: pydocs-mcp index . --branch feature/y]"
    )


def test_a_retired_live_branch_does_not_answer_the_empty_selector() -> None:
    merged = _row("feature/old", status=BranchStatus.MERGED, merged_into="main", retired_at=0.0)
    snap = _snap(_row("main", is_default=True), merged, live="feature/old")
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "main" and "--branch feature/old" in (resolved.suggestion or "")


def test_a_detached_or_non_git_checkout_gets_the_default_row_and_no_suggestion() -> None:
    snap = _snap(_row("detached-abc1234", is_default=True), live=None)
    resolved = resolve_branch_selector("", snap)
    assert resolved.name == "detached-abc1234" and resolved.suggestion is None


def test_empty_selector_on_a_bundle_without_rows_is_the_null_resolution() -> None:
    resolved = resolve_branch_selector("", BranchSnapshot((), None, None, {}))
    assert resolved == NULL_RESOLUTION
    assert resolved.name == "" and resolved.record is None and resolved.index_stale is False
    assert resolved.meta_name is None


def test_meta_name_hides_the_non_git_placeholder() -> None:
    snap = _snap(_row(NON_GIT_BRANCH_NAME, is_default=True), live=None)
    resolved = resolve_branch_selector("", snap)
    assert resolved.record is not None and resolved.meta_name is None
    assert resolve_branch_selector("", _snap(_row("main", is_default=True))).meta_name == "main"


def test_names_resolve_and_retired_rows_raise_the_precise_message() -> None:
    merged = _row(
        "feature/old",
        status=BranchStatus.MERGED,
        merged_into="main",
        landing_sha=B,
        retired_at=0.0,
    )
    snap = _snap(
        _row("main", is_default=True), merged, _row("feature/z", status=BranchStatus.INACTIVE)
    )
    assert resolve_branch_selector("main", snap).kind is BranchSelectorKind.NAME
    assert resolve_branch_selector("feature/z", snap).kind is BranchSelectorKind.NAME
    with pytest.raises(InvalidArgumentError, match="merged into main at bbbbbbb"):
        resolve_branch_selector("feature/old", snap)
    deleted = _row("gone", status=BranchStatus.DELETED, retired_at=0.0)
    with pytest.raises(InvalidArgumentError, match="'gone' was deleted locally"):
        resolve_branch_selector("gone", _snap(_row("main", is_default=True), deleted))


def test_an_unknown_name_raises_the_spec_sentence_naming_the_indexed_branches() -> None:
    merged = _row("feature/old", status=BranchStatus.MERGED, retired_at=0.0)
    snap = _snap(_row("main", is_default=True), _row("feature/x"), merged, _unit(U1))
    with pytest.raises(InvalidArgumentError) as caught:
        resolve_branch_selector("nope", snap)
    # §6.11: only the selectable branches are "indexed" — never a retired row
    # (it would raise) nor a landing unit (it is not a branch).
    assert str(caught.value) == (
        "no indexed branch 'nope'; indexed: ['feature/x', 'main']; "
        "run pydocs-mcp index . --branch nope"
    )


def test_a_non_git_bundle_names_no_placeholder_and_no_git_command() -> None:
    snap = _snap(_row(NON_GIT_BRANCH_NAME, is_default=True), live=None)
    with pytest.raises(InvalidArgumentError) as caught:
        resolve_branch_selector("nope", snap)
    # The placeholder is no branch (meta.branch hides it too), and a project
    # outside git has no ``--branch`` to index.
    assert str(caught.value) == (
        "no indexed branch 'nope'; the project is not a git repository, "
        "so it has no branches to select"
    )


def test_the_non_git_placeholder_is_not_selectable_by_name() -> None:
    snap = _snap(_row(NON_GIT_BRANCH_NAME, is_default=True), live=None)
    with pytest.raises(InvalidArgumentError, match="no indexed branch 'no git'"):
        resolve_branch_selector(NON_GIT_BRANCH_NAME, snap)


def test_a_detached_row_is_an_indexed_branch_listed_and_selectable() -> None:
    # Spec §2: a detached HEAD is the branch ``detached-<sha7>``, a real tree.
    snap = _snap(_row("main", is_default=True), _row("detached-abc1234"))
    assert resolve_branch_selector("detached-abc1234", snap).kind is BranchSelectorKind.NAME
    with pytest.raises(InvalidArgumentError, match=r"indexed: \['detached-abc1234', 'main'\]"):
        resolve_branch_selector("nope", snap)


def test_named_resolution_is_stale_only_when_both_heads_resolve_and_differ() -> None:
    snap = _snap(_row("main", is_default=True), _row("feature/x"), heads={"feature/x": B})
    assert resolve_branch_selector("feature/x", snap).index_stale is True
    assert resolve_branch_selector("main", snap).index_stale is False  # no live head read


def test_landing_shas_resolve_by_full_sha_then_unique_prefix() -> None:
    snap = _snap(_row("main", is_default=True), _unit(U1), _unit(U2, LandingKind.MERGE_COMMIT))
    resolved = resolve_branch_selector(U1, snap)
    assert resolved.kind is BranchSelectorKind.LANDING_SHA and resolved.is_landing_unit
    assert resolved.index_stale is False
    assert resolve_branch_selector("12345670", snap).name == U1
    with pytest.raises(InvalidArgumentError, match="matches 2 landing units"):
        resolve_branch_selector("1234567", snap)


def test_an_unknown_sha_raises_the_spec_sentence() -> None:
    snap = _snap(_row("main", is_default=True), _unit(U1))
    with pytest.raises(InvalidArgumentError) as caught:
        resolve_branch_selector("deadbee", snap)
    assert str(caught.value) == (
        "no branch or landing unit matches 'deadbee'; landings in the window: ['1234567']"
    )


def test_the_unknown_sha_error_never_lists_a_collected_unit() -> None:
    # §6.5b: a unit collected out of the window keeps its tombstone row forever
    # (INACTIVE, retired_at stamped); it is not a landing "in the window".
    collected = _unit("7654321" + "0" * 33, status=BranchStatus.INACTIVE, retired_at=5.0)
    snap = _snap(_row("main", is_default=True), _unit(U1), collected)
    with pytest.raises(InvalidArgumentError) as caught:
        resolve_branch_selector("deadbee", snap)
    assert str(caught.value).endswith("landings in the window: ['1234567']")


def test_the_unknown_sha_error_names_the_newest_landings_and_counts_the_rest() -> None:
    # The window holds up to ``max_landings`` units: the error stays one line.
    units = [_unit(f"{i:07x}" + "0" * 33, landed_at=float(i)) for i in range(1, 13)]
    snap = _snap(_row("main", is_default=True), *units)
    with pytest.raises(InvalidArgumentError) as caught:
        resolve_branch_selector("deadbee", snap)
    newest = [f"{i:07x}" for i in range(12, 2, -1)]
    assert str(caught.value) == (
        f"no branch or landing unit matches 'deadbee'; landings in the window: {newest} (+2 more)"
    )


def test_a_branch_literally_named_like_hex_wins_over_the_landing_lookup() -> None:
    snap = _snap(_row("main", is_default=True), _row("deadbeef"), _unit("deadbeef" + "0" * 32))
    assert resolve_branch_selector("deadbeef", snap).kind is BranchSelectorKind.NAME


def test_a_landing_unit_is_never_stale_even_when_its_live_ref_differs() -> None:
    snap = _snap(_row("main", is_default=True), _unit(U1), heads={U1: B})
    assert resolve_branch_selector(U1, snap).index_stale is False


async def test_a_known_unit_without_a_diff_resolves_as_a_landing_not_an_unknown_sha() -> None:
    """#316 creates the unit row at the MERGED transition; P2 generates its DIFF
    slice. In between the sha is KNOWN: it must resolve as a landing (what the
    tools answer for it is #315's split), never raise the unknown-sha error."""
    squash, base = "3" * 40, "2" * 40
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(_row("main", is_default=True))
        await uow.branches.upsert_branch(_row("feature/x"))
        await apply_merge_verdicts(
            uow,
            [MergeVerdict("feature/x", MergeEvidence.PATCH_ID_MATCH, squash, None)],
            base_name="main",
            now=100.0,
            policy=RetirementPolicy(
                grace_days=7, auto_retire_merged=True, auto_retire_deleted=True
            ),
            index=LandingIndex.from_steps((LandingStep(squash, (base,), 10.0, "x (#1)", "pid"),)),
        )
        assert await uow.branch_chunks.list_membership(squash) == ()
        await uow.commit()
    snapshot = await BranchDirectory(factory, None, ttl_seconds=0.0).snapshot()
    resolved = resolve_branch_selector(squash[:7], snapshot)
    assert resolved.kind is BranchSelectorKind.LANDING_SHA and resolved.name == squash
    with pytest.raises(InvalidArgumentError, match="merged into main at 3333333"):
        resolve_branch_selector("feature/x", snapshot)


def test_the_landing_unit_split_texts_name_the_tools_that_can_answer() -> None:
    err = landing_unit_error("1234567")
    assert isinstance(err, InvalidArgumentError)
    assert str(err) == (
        "'1234567' is a landing unit and has no tree; use search_codebase or grep with "
        "scope=diff, or name a branch"
    )
    assert landing_unit_suggestion("1234567") == (
        "[suggestion: landing unit 1234567 has no tree; use scope=diff or name a branch]"
    )
