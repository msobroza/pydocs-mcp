"""Footer + follow-up chips — AC-17, AC-18, AC-31, AC-34."""

from __future__ import annotations

import random

from pydocs_mcp.harness.ask_your_docs.answer_footer import (
    FollowUpKind,
    apply_follow_up_chip,
    derive_follow_up_chips,
    render_answer_footer,
)
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.models import BranchStatus

_SHA = "3e1a9c2" + "0" * 33
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
U2 = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)


def _row(
    name,
    *,
    default=False,
    base=None,
    status=BranchStatus.ACTIVE,
    merged_into=None,
    sha="3e1a9c2" + "f" * 33,
):
    return IndexedBranch(name, sha, base, default, status, merged_into, None, 1.0)


LISTING = WorkspaceBranchListing(
    projects={
        "backend": (
            _row("feature/retry", default=True, base="main"),
            _row("main", sha="9abcdef" + "0" * 33),
            _row("feature/old", status=BranchStatus.MERGED, merged_into=_SHA),
        ),
        "tooling": (_row("main", default=True),),
    }
)
SINGLE = WorkspaceBranchListing(projects={"demo": ()})


def _obs(
    tool="search_codebase",
    project="backend",
    branch="feature/retry",
    origin=BranchOrigin.PINNED,
    slice_=ScopeSlice.WHOLE_BRANCH,
    meta=None,
    replaced=False,
):
    return CellObservation(
        tool, project, branch, origin, slice_, meta or {"branch": branch or None}, replaced
    )


def _observations(*records) -> ScopeObservations:
    observations = ScopeObservations()
    for record in records:
        observations.append(record)
    return observations


DEFAULTS = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))


class TestFooter:  # AC-18, AC-34
    def test_pinned_cell_segment(self):
        footer = render_answer_footer(_observations(_obs(), _obs(tool="get_symbol")), LISTING)
        assert footer == "answered from backend · feature/retry @3e1a9c2 · whole branch · pinned"

    def test_segments_are_sorted_and_joined_with_bars(self):
        footer = render_answer_footer(
            _observations(
                _obs(project="tooling", branch="main"), _obs(project="backend", branch="main")
            ),
            LISTING,
        )
        assert footer == (
            "answered from backend · main @9abcdef · whole branch · pinned"
            " | answered from tooling · main @3e1a9c2 · whole branch · pinned"
        )

    def test_union_answer_on_a_multi_project_listing_reads_all_projects(self):
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            meta={"project": "backend", "branch": "main", "indexed_git_head": "abcdef0123"},
        )
        footer = render_answer_footer(_observations(record), LISTING)
        assert (
            footer == "answered from all projects · main @abcdef0 · whole branch · server default"
        )

    def test_replaced_argument_and_stale_index_are_visible(self):
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            replaced=True,
            meta={"project": "demo", "index_stale": True},
        )
        footer = render_answer_footer(_observations(record), SINGLE)
        assert footer == "answered from demo · no branch · agent-chosen → default · index stale"

    def test_pre_v16_bundle_footer(self):
        """AC-34: no branch, no sha, server default."""
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            meta={"project": "demo", "branch": None},
        )
        assert (
            render_answer_footer(_observations(record), SINGLE)
            == "answered from demo · no branch · server default"
        )

    def test_distinct_slices_are_listed(self):
        footer = render_answer_footer(
            _observations(_obs(slice_=ScopeSlice.DIFF_HUNKS), _obs(slice_=ScopeSlice.WHOLE_BRANCH)),
            LISTING,
        )
        assert "· whole branch, diff hunks ·" in footer

    def test_mixed_origins_name_the_most_specific_one(self):
        """Neither the first nor the last record's origin: the precedence table decides."""
        records = [
            _obs(origin=BranchOrigin.SERVER),
            _obs(origin=BranchOrigin.AGENT_CHOSEN),
            _obs(origin=BranchOrigin.DEFAULT),
        ]
        footer = render_answer_footer(_observations(*records), LISTING)
        assert footer.endswith("· whole branch · agent-chosen")

    def test_no_tool_calls(self):
        assert render_answer_footer(ScopeObservations(), LISTING) == "answered without tool calls"


class TestChips:  # AC-17
    def test_one_answered_cell_with_a_base_yields_compare_and_pin(self):
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None
        )
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.PIN_BRANCH]
        assert chips[0].label == "compare with main"
        assert chips[0].branches == ("feature/retry", "main")
        assert chips[0].question == (
            "Compare the previous answer between feature/retry and main: what differs?"
        )
        assert chips[1].label == "pin feature/retry"

    def test_nothing_on_u0(self):
        observations = _observations(_obs(origin=BranchOrigin.DEFAULT))
        assert derive_follow_up_chips(observations, LISTING, NO_SCOPE_CAPABILITIES, None) == ()

    def test_pinned_cells_and_kept_cells_get_no_pin_chip(self):
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U1, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH]
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "feature/retry"),))
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.AGENT_CHOSEN)), LISTING, U1, kept
        )
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH]

    def test_two_answered_cells_yield_two_pin_chips_in_order_and_no_compare(self):
        # The backend cell is a server-resolved branch reported through meta and
        # HAS a base (feature/retry -> main): a compare chip would appear if the
        # single-cell guard were dropped.
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(
                project="backend",
                branch="",
                origin=BranchOrigin.SERVER,
                meta={"branch": "feature/retry"},
            ),
        ]
        chips = derive_follow_up_chips(_observations(*records), LISTING, U1, None)
        assert [(c.kind, c.project, c.branches) for c in chips] == [
            (FollowUpKind.PIN_BRANCH, "backend", ("feature/retry",)),
            (FollowUpKind.PIN_BRANCH, "tooling", ("main",)),
        ]

    def test_show_the_diff_needs_u2_and_no_prior_diff_slice(self):
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U2, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.SHOW_DIFF]
        assert chips[1].slice is ScopeSlice.DIFF_HUNKS and chips[1].branches == ("feature/retry",)
        chips = derive_follow_up_chips(
            _observations(_obs(slice_=ScopeSlice.DIFF_HUNKS)), LISTING, U2, None
        )
        assert FollowUpKind.SHOW_DIFF not in [c.kind for c in chips]

    def test_show_the_diff_on_a_tombstone_targets_the_landing_sha(self):
        chips = derive_follow_up_chips(
            _observations(_obs(branch="feature/old", origin=BranchOrigin.AGENT_CHOSEN)),
            LISTING,
            U2,
            None,
        )
        diff = next(c for c in chips if c.kind is FollowUpKind.SHOW_DIFF)
        assert diff.branches == (_SHA,)

    def test_at_most_three_and_deterministic_under_shuffle(self):
        records = [
            _obs(project=p, branch="main", origin=BranchOrigin.DEFAULT)
            for p in ("backend", "tooling")
        ]
        records.append(_obs(project="backend", branch="feature/retry", origin=BranchOrigin.DEFAULT))
        random.Random(7).shuffle(records)
        chips = derive_follow_up_chips(_observations(*records), LISTING, U2, None)
        assert len(chips) == 3 and [c.kind for c in chips] == [FollowUpKind.PIN_BRANCH] * 3
        assert [(c.project, c.branches[0]) for c in chips] == [
            ("backend", "feature/retry"),
            ("backend", "main"),
            ("tooling", "main"),
        ]

    def test_a_fourth_answered_cell_is_cut_by_the_kind_count_cap(self):
        cells = (("backend", "main"), ("tooling", "main"), ("backend", "feature/retry"))
        records = [_obs(project=p, branch=b, origin=BranchOrigin.DEFAULT) for p, b in cells]
        records.append(_obs(project="backend", branch="feature/old", origin=BranchOrigin.DEFAULT))
        chips = derive_follow_up_chips(_observations(*records), LISTING, U2, None)
        assert len(chips) == len(FollowUpKind) == 3
        assert [(c.project, c.branches[0]) for c in chips] == [
            ("backend", "feature/old"),
            ("backend", "feature/retry"),
            ("backend", "main"),
        ]

    def test_union_records_on_a_multi_project_listing_yield_nothing(self):
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            meta={"project": "backend", "branch": "main"},
        )
        assert derive_follow_up_chips(_observations(record), LISTING, U1, None) == ()


class TestApply:  # AC-31
    def test_compare_returns_the_question_and_a_one_shot_pin_leaving_kept_alone(self):
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
        (chip, _) = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None
        )
        question, pin = apply_follow_up_chip(chip, kept, DEFAULTS)
        assert question == chip.question
        assert pin.cells == (ScopeCell("backend", "feature/retry"), ScopeCell("backend", "main"))
        assert kept.cells == (ScopeCell("backend", "main"),)

    def test_pin_branch_returns_no_question_and_grows_the_kept_pin(self):
        (_, chip) = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None
        )
        question, pin = apply_follow_up_chip(chip, None, DEFAULTS)
        assert question is None and pin.cells == (ScopeCell("backend", "feature/retry"),)
        kept = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"),))
        _, grown = apply_follow_up_chip(chip, kept, DEFAULTS)
        assert grown.cells == (ScopeCell("tooling", "main"), ScopeCell("backend", "feature/retry"))

    def test_pin_branch_without_a_kept_pin_carries_the_session_defaults(self):
        defaults = QuestionScope(
            kind=ScopeKind.DEFAULT,
            cells=(ScopeCell("", ""),),
            code=ScopeCode.OWN,
            package="fastapi",
        )
        (_, chip) = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None
        )
        _, pin = apply_follow_up_chip(chip, None, defaults)
        assert pin.kind is ScopeKind.PIN
        assert (pin.code, pin.package) == (ScopeCode.OWN, "fastapi")

    def test_show_diff_drops_a_dependencies_only_default(self):
        defaults = QuestionScope(
            kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.DEPS
        )
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U2, None)
        diff = next(c for c in chips if c.kind is FollowUpKind.SHOW_DIFF)
        question, pin = apply_follow_up_chip(diff, None, defaults)
        assert question == "Show the diff hunks behind the previous answer."
        assert pin.slice is ScopeSlice.DIFF_HUNKS and pin.code is ScopeCode.ALL
