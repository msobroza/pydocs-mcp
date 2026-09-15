"""Footer + follow-up chips — AC-17, AC-18, AC-31, AC-34, AC-43, AC-46."""

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
    SLICE_LABELS,
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
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

_SHA = "3e1a9c2" + "0" * 33
U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
U2 = ScopeCapabilities(branch_selector=True, changed_slice=True, diff_slice=True)
# The two YAML keys of the teaching hint (§7): QUIET silences it so a segment
# assertion reads the segments alone; HINTS is the shipped pair.
QUIET = ScopeDefaultsConfig(footer_hint=False)
HINTS = ScopeDefaultsConfig()
CHANGED_WORDS = SLICE_LABELS[ScopeSlice.CHANGED_FILES]
DIFF_WORDS = SLICE_LABELS[ScopeSlice.DIFF_HUNKS]


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
# A third live branch, so "Ask this on <branch> too" has a target that is neither the
# answered branch nor the base "Compare with" names (§6.9); feature/spare is the LAST
# pickable row, so a chip that took the first one would name feature/retry itself.
THREE_BRANCH = WorkspaceBranchListing(
    projects={
        "backend": (
            _row("feature/retry", default=True, base="main"),
            _row("main", sha="9abcdef" + "0" * 33),
            _row("feature/spare"),
        ),
        "tooling": (_row("main", default=True),),
    }
)
# Listing order is NOT alphabetical order: a hint that sorted the names would say
# "api", and one that read the listing says "backend" (§6.8).
UNSORTED = WorkspaceBranchListing(
    projects={
        "tooling": (_row("main", default=True),),
        "backend": (_row("main", default=True),),
        "api": (_row("main", default=True),),
    }
)


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
        footer = render_answer_footer(
            _observations(_obs(), _obs(tool="get_symbol")), LISTING, QUIET
        )
        assert footer == "Searched backend · feature/retry @3e1a9c2 (only these) · index up to date"

    def test_segments_are_sorted_and_joined_with_bars(self):
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(project="backend", branch="main", origin=BranchOrigin.PINNED),
        ]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == (
            "Searched backend · main @9abcdef (only these) · index up to date"
            " | tooling · main @3e1a9c2 (your default) · index up to date"
        )

    def test_union_answer_on_a_multi_project_listing_reads_all_projects(self):
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            meta={"project": "backend", "branch": "main", "indexed_git_head": "9abcdef" + "0" * 33},
        )
        footer = render_answer_footer(_observations(record), LISTING, QUIET)
        assert footer == (
            "Searched all projects · main @9abcdef (the server's default) · index up to date"
        )

    def test_replaced_argument_and_stale_index_are_visible(self):
        record = _obs(
            origin=BranchOrigin.DEFAULT,
            replaced=True,
            meta={"branch": "feature/retry", "index_stale": True},
        )
        footer = render_answer_footer(_observations(record), LISTING, QUIET)
        assert footer == (
            "Searched backend · feature/retry @3e1a9c2 (the agent's choice → your default)"
            " · index behind your checkout — reindex to search it"
        )

    def test_pre_v16_bundle_footer(self):
        """AC-34: no listing rows, no meta.branch, no sha — freshness still present."""
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"project": "demo"})
        footer = render_answer_footer(_observations(record), SINGLE, QUIET)
        assert footer == "Searched demo · no branch (the server's default) · index up to date"

    def test_distinct_slices_are_listed_after_the_origin_and_the_default_is_omitted(self):
        records = [
            _obs(slice_=ScopeSlice.CHANGED_FILES),
            _obs(slice_=ScopeSlice.DIFF_HUNKS),
            _obs(),  # the whole-branch default: never named
        ]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == (
            "Searched backend · feature/retry @3e1a9c2 (only these)"
            f" · {CHANGED_WORDS}, {DIFF_WORDS} · index up to date"
        )

    def test_mixed_origins_name_the_most_specific_one(self):
        records = [_obs(origin=BranchOrigin.SERVER), _obs(origin=BranchOrigin.AGENT_CHOSEN)]
        footer = render_answer_footer(_observations(*records), LISTING, QUIET)
        assert footer == (
            "Searched backend · feature/retry @3e1a9c2 (the agent's choice) · index up to date"
        )

    def test_no_tool_calls(self):
        assert (
            render_answer_footer(_observations(), LISTING, QUIET) == "answered without tool calls"
        )


class TestHint:  # AC-43
    def test_names_the_first_unsearched_project_in_listing_order(self):
        footer = render_answer_footer(
            _observations(_obs(project="tooling", branch="main")), UNSORTED, HINTS
        )
        assert footer.endswith(" · add in:backend to search there too")
        assert "api" not in footer  # the alphabetically first unsearched name

    def test_no_hint_when_every_project_was_searched(self):
        records = [_obs(project="tooling", branch="main"), _obs()]
        footer = render_answer_footer(_observations(*records), LISTING, HINTS)
        assert "add in:" not in footer

    def test_a_union_answer_searched_every_project_and_carries_no_hint(self):
        record = _obs(project="", branch="", origin=BranchOrigin.SERVER, meta={"branch": "main"})
        footer = render_answer_footer(_observations(record), UNSORTED, HINTS)
        assert "add in:" not in footer and "add on:" not in footer

    def test_no_hint_when_either_key_is_off(self):
        obs = _observations(_obs(project="tooling", branch="main"))
        quiet_tokens = render_answer_footer(obs, LISTING, ScopeDefaultsConfig(tokens_enabled=False))
        quiet_hint = render_answer_footer(obs, LISTING, ScopeDefaultsConfig(footer_hint=False))
        assert "add in:" not in quiet_tokens and "add in:" not in quiet_hint

    def test_u1_one_cell_with_an_indexed_base_teaches_on(self):
        footer = render_answer_footer(_observations(_obs()), LISTING, HINTS, U1)
        assert footer.endswith(" · add on:main to compare with main")
        # Same records, no branch capability: the in: form, never on:.
        without = render_answer_footer(_observations(_obs()), LISTING, HINTS)
        assert without.endswith(" · add in:tooling to search there too")

    def test_no_on_hint_when_the_base_is_not_indexed(self):
        listing = WorkspaceBranchListing(
            projects={"backend": (_row("feature/retry", default=True, base="main"),)}
        )
        footer = render_answer_footer(_observations(_obs()), listing, HINTS, U1)
        assert "add on:" not in footer and "add in:" not in footer


class TestChips:  # AC-17, AC-46
    def test_one_answered_cell_with_a_spare_branch_yields_ask_on_compare_and_keep(self):
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), THREE_BRANCH, U1, None, asked="why?"
        )
        assert [c.kind for c in chips] == [
            FollowUpKind.ASK_ON,
            FollowUpKind.COMPARE_WITH,
            FollowUpKind.PIN_BRANCH,
        ]
        ask_on, compare, keep = chips
        # ASK_ON names the first OTHER pickable branch that "Compare with" does not.
        assert ask_on.label == "Ask this on feature/spare too"
        assert ask_on.branches == ("feature/spare",) and ask_on.question == "why?"
        assert compare.label == "Compare with main"
        assert compare.question == (
            "Compare the previous answer between feature/retry and main: what differs?"
        )
        assert keep.label == "Keep searching feature/retry" and keep.question == ""

    def test_ask_on_never_names_the_base_a_compare_chip_names(self):
        """AC-46: a two-branch project shows "Compare with main" alone."""
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, None, asked="why?"
        )
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.PIN_BRANCH]

    def test_ask_on_needs_the_branch_capability_and_the_question(self):
        observations = _observations(_obs(origin=BranchOrigin.DEFAULT))
        on_u0 = derive_follow_up_chips(
            observations, THREE_BRANCH, NO_SCOPE_CAPABILITIES, None, asked="why?"
        )
        assert on_u0 == ()
        unasked = derive_follow_up_chips(observations, THREE_BRANCH, U1, None)
        assert FollowUpKind.ASK_ON not in [c.kind for c in unasked]

    def test_ask_on_is_absent_without_another_branch(self):
        one_branch = WorkspaceBranchListing(projects={"tooling": (_row("main", default=True),)})
        chips = derive_follow_up_chips(
            _observations(_obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT)),
            one_branch,
            U1,
            None,
            asked="why?",
        )
        assert FollowUpKind.ASK_ON not in [c.kind for c in chips]

    def test_nothing_on_u0(self):
        observations = _observations(_obs(origin=BranchOrigin.DEFAULT))
        assert derive_follow_up_chips(observations, LISTING, NO_SCOPE_CAPABILITIES, None) == ()

    def test_pinned_cells_and_strip_cells_get_no_keep_chip(self):
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U1, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH]
        strip_scope = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("backend", "feature/retry"),)
        )
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.AGENT_CHOSEN)), LISTING, U1, strip_scope
        )
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH]

    def test_two_answered_cells_yield_one_keep_chip_and_nothing_else(self):
        """D14: at most one chip per KIND — the 2026-09-04 rule gave one per cell."""
        records = [
            _obs(project="tooling", branch="main", origin=BranchOrigin.DEFAULT),
            _obs(
                project="backend",
                branch="",
                origin=BranchOrigin.SERVER,
                meta={"branch": "feature/retry"},
            ),
        ]
        chips = derive_follow_up_chips(_observations(*records), LISTING, U1, None, asked="why?")
        assert [(c.kind, c.project, c.branches) for c in chips] == [
            (FollowUpKind.PIN_BRANCH, "backend", ("feature/retry",))  # first in (project, branch)
        ]

    def test_show_the_diff_needs_u2_and_no_prior_diff_slice(self):
        chips = derive_follow_up_chips(_observations(_obs()), LISTING, U2, None)
        assert [c.kind for c in chips] == [FollowUpKind.COMPARE_WITH, FollowUpKind.SHOW_DIFF]
        assert chips[1].label == "Show what changed"
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

    def test_at_most_one_chip_per_kind_and_deterministic_under_shuffle(self):
        records = [
            _obs(project=p, branch="main", origin=BranchOrigin.DEFAULT)
            for p in ("backend", "tooling")
        ]
        records.append(_obs(project="backend", branch="feature/retry", origin=BranchOrigin.DEFAULT))
        random.Random(7).shuffle(records)
        chips = derive_follow_up_chips(_observations(*records), LISTING, U2, None, asked="why?")
        kinds = [c.kind for c in chips]
        assert len(kinds) == len(set(kinds))
        assert len(chips) <= len(FollowUpKind) == 4
        assert [(c.kind, c.project, c.branches[0]) for c in chips] == [
            (FollowUpKind.PIN_BRANCH, "backend", "feature/retry")
        ]

    def test_keep_searching_is_suppressed_when_the_strip_is_at_the_cap(self):
        """A chip that grew the strip past max_cells would make every later send fail
        at E4. Strip of two cells, cap 2: no keep chip; cap 3: the chip is back."""
        strip_scope = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"), ScopeCell("backend", "main"))
        )
        obs = _observations(_obs(origin=BranchOrigin.DEFAULT))  # backend · feature/retry
        at_cap = derive_follow_up_chips(obs, LISTING, U1, strip_scope, max_cells=2)
        assert FollowUpKind.PIN_BRANCH not in [c.kind for c in at_cap]
        assert FollowUpKind.COMPARE_WITH in [c.kind for c in at_cap]  # only the keep chip is gated
        room = derive_follow_up_chips(obs, LISTING, U1, strip_scope, max_cells=3)
        assert FollowUpKind.PIN_BRANCH in [c.kind for c in room]

    def test_a_default_strip_scope_does_not_count_against_the_cap(self):
        """A no-target strip compiles to DEFAULT with the union cell: no strip cell."""
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), LISTING, U1, DEFAULTS, max_cells=1
        )
        assert FollowUpKind.PIN_BRANCH in [c.kind for c in chips]

    def test_union_records_on_a_multi_project_listing_yield_nothing(self):
        record = _obs(
            project="",
            branch="",
            origin=BranchOrigin.SERVER,
            meta={"project": "backend", "branch": "main"},
        )
        assert derive_follow_up_chips(_observations(record), LISTING, U1, None) == ()


class TestApply:  # AC-31, AC-46
    """Three values every time — the question, the one-shot pin and the strip the chat page
    writes back. A StripState comes back, never a grown scope: ``compile_strip_scope`` is
    one-way, so the page could not turn a scope back into targets (§6.9)."""

    def _chip(self, kind, listing=LISTING, asked=""):
        chips = derive_follow_up_chips(
            _observations(_obs(origin=BranchOrigin.DEFAULT)), listing, U2, None, asked=asked
        )
        return next(c for c in chips if c.kind is kind)

    def test_compare_returns_the_question_and_a_one_shot_pin_leaving_the_strip_alone(self):
        chip = self._chip(FollowUpKind.COMPARE_WITH)
        strip = StripState(targets=(StripTarget("backend", ("main",)),), only_these=True)
        question, pin, after = apply_follow_up_chip(chip, strip, DEFAULTS)
        assert question == chip.question
        assert pin.cells == (ScopeCell("backend", "feature/retry"), ScopeCell("backend", "main"))
        assert after is strip  # untouched before, during and after the send

    def test_ask_on_returns_the_same_question_under_a_one_shot_pin_on_the_other_branch(self):
        chip = self._chip(FollowUpKind.ASK_ON, THREE_BRANCH, asked="why does retry drop?")
        strip = StripState()
        question, pin, after = apply_follow_up_chip(chip, strip, DEFAULTS)
        assert question == "why does retry drop?"
        assert pin == QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("backend", "feature/spare"),)
        )
        assert after is strip

    def test_keep_searching_appends_a_target_for_a_project_the_strip_lacks(self):
        chip = self._chip(FollowUpKind.PIN_BRANCH)
        strip = StripState(targets=(StripTarget("tooling", ("main",)),), only_these=True)
        question, pin, grown = apply_follow_up_chip(chip, strip, DEFAULTS)
        assert (question, pin) == (None, None)  # a PIN_BRANCH click sends nothing
        assert grown.targets == (
            StripTarget("tooling", ("main",)),
            StripTarget("backend", ("feature/retry",)),
        )
        assert grown.only_these is True  # the input's: the >= 2 rule is a screen rule (E16)

    def test_keep_searching_adds_one_branch_to_a_held_project_in_place(self):
        """Position is kept: the strip renders in target order, so a grown target must not
        jump to the end of the row."""
        chip = self._chip(FollowUpKind.PIN_BRANCH)
        strip = StripState(
            targets=(StripTarget("backend", ("main",)), StripTarget("tooling", ("main",))),
            only_these=True,
        )
        _, _, grown = apply_follow_up_chip(chip, strip, DEFAULTS)
        assert grown.targets == (
            StripTarget("backend", ("main", "feature/retry")),
            StripTarget("tooling", ("main",)),
        )
        assert grown.only_these is True

    def test_keep_searching_on_an_empty_strip_starts_one_target(self):
        """No pin rides a PIN_BRANCH click: the strip holds targets, and compile_strip_scope
        is what layers the session's "More" values over YAML afterwards."""
        chip = self._chip(FollowUpKind.PIN_BRANCH)
        question, pin, grown = apply_follow_up_chip(chip, StripState(), DEFAULTS)
        assert (question, pin) == (None, None)
        assert grown.targets == (StripTarget("backend", ("feature/retry",)),)
        assert grown.only_these is False

    def test_show_diff_drops_a_dependencies_only_default(self):
        defaults = QuestionScope(
            kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.DEPS
        )
        strip = StripState()
        question, pin, after = apply_follow_up_chip(
            self._chip(FollowUpKind.SHOW_DIFF), strip, defaults
        )
        assert question == "Show the diff hunks behind the previous answer."
        assert pin.slice is ScopeSlice.DIFF_HUNKS and pin.code is ScopeCode.ALL
        assert after is strip
