"""The strip's state and its compiler — AC-35 (``compile_strip_scope``), the R3 seed rule.

``tooling`` is the target throughout: it is neither ``ScopeDefaultsConfig().project``
(``any``) nor ``_LISTING``'s first row, so DEFAULT and PIN carry different cells and a
"first project wins" mutant is caught.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeBranchDefault,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
    resolve_default_branch,
    resolve_question_scope_defaults,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    StripTarget,
    compile_strip_scope,
    initial_strip_state,
    missing_strip_cells,
    ordered_targets,
    strip_cells,
    strip_chip_label,
    strip_target_for,
)
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = ScopeDefaultsConfig()
# The U1 capability record: a server that takes a "branch" argument.
_BRANCH_CAPABLE = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


def _row(name: str, *, default: bool = False, base: str | None = None) -> IndexedBranch:
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


# The two projects DISAGREE on default vs base: backend's stamped row is feature/x
# (whose base is main), tooling's is main with no base — and backend's stamped row
# is deliberately NOT its first row, so a compiler that reached for the base, the
# first row, or the first name differs from the stamped row on backend.
_LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("main"), _row("feature/x", default=True, base="main")),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789"}),
)
# Listing order is dict insertion order (catalog.project_names), and _LISTING's
# (backend, tooling) is ALSO alphabetical — so a compiler that sorted by name would
# pass against it by luck. This listing lists tooling FIRST: the three candidate
# orders now all differ — listing (tooling, backend) != sorted (backend, tooling) !=
# the strip's insertion order below (backend, tooling).
_REVERSED = WorkspaceBranchListing(
    projects={"tooling": _LISTING.rows("tooling"), "backend": _LISTING.rows("backend")}
)


def _target(project: str, *branches: str) -> StripTarget:
    return StripTarget(project, branches)


class TestCompileStripScope:  # AC-35
    def test_no_target_is_todays_default_path_byte_identical(self) -> None:
        # The R11 fence: the strip's empty state IS resolve_question_scope_defaults.
        expected = resolve_question_scope_defaults(_CONFIG, ScopeDefaultsOverride(), _LISTING)
        assert compile_strip_scope((), False, _CONFIG, _LISTING) == expected
        # No row 2 without a target, so the tick is moot either way.
        assert compile_strip_scope((), True, _CONFIG, _LISTING) == expected

    def test_no_target_means_the_union_even_when_yaml_names_a_project(self) -> None:
        # "Clear" empties the strip; the YAML project is the INITIAL state (Reset), not a floor.
        scope = compile_strip_scope((), False, ScopeDefaultsConfig(project="backend"), _LISTING)
        assert scope.kind is ScopeKind.DEFAULT
        assert scope.cells == (ScopeCell("", ""),)

    def test_one_target_only_these_off_is_a_soft_default_for_that_project(self) -> None:
        scope = compile_strip_scope((_target("tooling", "main"),), False, _CONFIG, _LISTING)
        assert scope.kind is ScopeKind.DEFAULT
        assert scope.cells == (ScopeCell("tooling", ""),)  # the branch is resolved per call
        # On U0 nothing branch-shaped is sent: the chip's branch stays informational.
        assert (scope.branch_default, scope.branch_name) == (ScopeBranchDefault.BASE, "")

    def test_a_branch_capable_server_sends_the_branch_the_chip_shows(self) -> None:
        """AC-35 (U1 half): release/2 is neither backend's stamped row nor its base,
        so a mutant that ignored the target's branch resolves to "main" instead."""
        listing = WorkspaceBranchListing(
            projects={
                "backend": (
                    _row("feature/x", default=True, base="main"),
                    _row("main"),
                    _row("release/2"),
                )
            }
        )
        targets = (_target("backend", "release/2"),)
        scope = compile_strip_scope(targets, False, _CONFIG, listing, capabilities=_BRANCH_CAPABLE)
        assert scope.kind is ScopeKind.DEFAULT and scope.branch_name == "release/2"
        assert resolve_default_branch(scope, "backend", listing) == "release/2"
        # The same target on a server that takes no branch keeps YAML's value.
        u0 = compile_strip_scope(targets, False, _CONFIG, listing)
        assert u0.branch_name == "" and resolve_default_branch(u0, "backend", listing) == "main"

    def test_two_branches_on_one_target_never_narrow_a_soft_default(self) -> None:
        """Two cells are a PIN, so the "which branch" question the U1 override answers
        does not arise; the fan-out carries both branches instead."""
        scope = compile_strip_scope(
            (_target("backend", "feature/x", "main"),),
            False,
            _CONFIG,
            _LISTING,
            capabilities=_BRANCH_CAPABLE,
        )
        assert scope.kind is ScopeKind.PIN
        assert scope.cells == (ScopeCell("backend", "feature/x"), ScopeCell("backend", "main"))

    def test_one_target_only_these_on_pins_that_one_cell(self) -> None:
        scope = compile_strip_scope((_target("tooling", "main"),), True, _CONFIG, _LISTING)
        assert scope.kind is ScopeKind.PIN
        assert scope.cells == (ScopeCell("tooling", "main"),)

    def test_two_targets_pin_in_listing_order_whatever_the_checkbox_holds(self) -> None:
        targets = (_target("backend", "feature/x"), _target("tooling", "main"))
        for only_these in (False, True):
            scope = compile_strip_scope(targets, only_these, _CONFIG, _REVERSED)
            assert scope.kind is ScopeKind.PIN
            # tooling first: the LISTING order, which is neither sorted nor as inserted.
            assert scope.cells == (ScopeCell("tooling", "main"), ScopeCell("backend", "feature/x"))

    def test_a_target_with_no_branch_rows_pins_the_bare_project(self) -> None:
        """E8 — a pre-v16 bundle: the one empty-branch cell shape."""
        scope = compile_strip_scope((StripTarget("tooling", ()),), True, _CONFIG, _LISTING)
        assert scope.cells == (ScopeCell("tooling", ""),)

    def test_a_project_the_listing_does_not_know_sorts_last(self) -> None:
        targets = (_target("frontend", "main"), _target("tooling", "main"))
        scope = compile_strip_scope(targets, False, _CONFIG, _LISTING)
        assert scope.cells == (ScopeCell("tooling", "main"), ScopeCell("frontend", "main"))

    def test_more_values_ride_through_every_case(self) -> None:
        more = ScopeDefaultsOverride(code=ScopeCode.OWN, package="fastapi")
        soft = compile_strip_scope((), False, _CONFIG, _LISTING, more=more)
        one = compile_strip_scope(
            (_target("tooling", "main"),), False, _CONFIG, _LISTING, more=more
        )
        pinned = compile_strip_scope(
            (_target("tooling", "main"),), True, _CONFIG, _LISTING, more=more
        )
        for scope in (soft, one, pinned):
            assert (scope.code, scope.package) == (ScopeCode.OWN, "fastapi")

    def test_a_slice_never_combines_with_dependencies_only(self) -> None:
        """E11 under both kinds: the value object REFUSES the pair, so the strip widens."""
        sliced = ScopeDefaultsOverride(code=ScopeCode.DEPS, slice=ScopeSlice.DIFF_HUNKS)
        pinned = compile_strip_scope(
            (_target("tooling", "main"),), True, _CONFIG, _LISTING, more=sliced
        )
        soft = compile_strip_scope((), False, _CONFIG, _LISTING, more=sliced)
        assert pinned.code is ScopeCode.ALL and pinned.slice is ScopeSlice.DIFF_HUNKS
        assert soft.code is ScopeCode.ALL and soft.slice is ScopeSlice.DIFF_HUNKS

    def test_a_yaml_slice_widens_a_more_value_of_dependencies_only(self) -> None:
        # The offending pair can also straddle the two layers: YAML's slice, the
        # picker's code. The widening reads the EFFECTIVE values, not the override's.
        config = ScopeDefaultsConfig(slice=ScopeSlice.CHANGED_FILES)
        scope = compile_strip_scope(
            (_target("tooling", "main"),),
            True,
            config,
            _LISTING,
            more=ScopeDefaultsOverride(code=ScopeCode.DEPS),
        )
        assert scope.code is ScopeCode.ALL and scope.slice is ScopeSlice.CHANGED_FILES


class TestInitialStripState:  # R3's seed rule
    def test_the_shipped_yaml_seeds_no_target(self) -> None:
        assert initial_strip_state(_CONFIG, _LISTING) == StripState()

    def test_a_named_project_seeds_its_stamped_row(self) -> None:
        named = initial_strip_state(ScopeDefaultsConfig(project="backend"), _LISTING)
        # The STAMPED row (feature/x), not the base (main): the fixture disagrees on purpose.
        assert named.targets == (StripTarget("backend", ("feature/x",)),)
        assert named.only_these is False
        assert named.more == ScopeDefaultsOverride()  # "More" starts at the YAML values

    def test_a_bundle_stem_seeds_the_project_it_names(self) -> None:
        # The project= selector's second spelling; one cell shape means one spelling.
        seeded = initial_strip_state(ScopeDefaultsConfig(project="backend_0123456789"), _LISTING)
        assert seeded.targets == (StripTarget("backend", ("feature/x",)),)

    def test_an_unlisted_project_seeds_no_target_and_logs(self, caplog) -> None:
        with caplog.at_level("INFO"):
            unknown = initial_strip_state(ScopeDefaultsConfig(project="frontend"), _LISTING)
        assert unknown.targets == ()  # an unlisted YAML project seeds no target (AC-3's rule)…
        record = json.loads(
            caplog.records[-1].getMessage()
        )  # …and logs it, as the per-call path does
        assert (record["event"], record["argument"], record["passed"], record["replacement"]) == (
            "scope_default_replaced",
            "project",
            "frontend",
            "",
        )

    def test_an_unscanned_workspace_passes_the_name_through(self) -> None:
        # Mirrors resolve_question_scope_defaults on an empty listing: nothing was
        # scanned, so the name is not "unknown" yet and must not be replaced.
        seeded = initial_strip_state(ScopeDefaultsConfig(project="backend"), EMPTY_BRANCH_LISTING)
        assert seeded.targets == (StripTarget("backend", ()),)
        scope = compile_strip_scope(seeded.targets, False, _CONFIG, EMPTY_BRANCH_LISTING)
        assert scope.cells == (ScopeCell("backend", ""),)


class TestStripStateEdits:
    _STATE = StripState(
        (StripTarget("backend", ("feature/x", "main")), StripTarget("tooling", ("main",))),
        only_these=True,
    )

    def test_removing_one_chip_leaves_the_other_branches(self) -> None:
        assert self._STATE.without_cell(ScopeCell("backend", "main")).targets == (
            StripTarget("backend", ("feature/x",)),
            StripTarget("tooling", ("main",)),
        )

    def test_removing_a_projects_last_chip_removes_the_target(self) -> None:
        assert self._STATE.without_cell(ScopeCell("tooling", "main")).targets == (
            StripTarget("backend", ("feature/x", "main")),
        )
        # The user's tick survives an edit: only "Clear" drops it.
        assert self._STATE.without_cell(ScopeCell("tooling", "main")).only_these is True

    def test_adding_a_target_merges_branches_into_the_project_in_place(self) -> None:
        assert self._STATE.with_target(StripTarget("backend", ("main",))) == self._STATE
        grown = self._STATE.with_target(StripTarget("backend", ("release",)))
        assert grown.targets[0] == StripTarget("backend", ("feature/x", "main", "release"))
        assert grown.projects() == ("backend", "tooling")  # position kept, not re-appended

    def test_a_new_project_joins_the_end(self) -> None:
        grown = self._STATE.with_target(StripTarget("frontend", ("main",)))
        assert grown.projects() == ("backend", "tooling", "frontend")

    def test_clear_empties_the_strip_and_the_tick_but_keeps_more(self) -> None:
        state = StripState(self._STATE.targets, True, ScopeDefaultsOverride(package="fastapi"))
        cleared = state.cleared()
        assert cleared.targets == () and cleared.more == state.more
        assert cleared.only_these is False  # no target: the tick is moot and does not linger


class TestStripHelpers:
    def test_strip_target_for_takes_the_stamped_row(self) -> None:
        assert strip_target_for("backend", _LISTING) == StripTarget("backend", ("feature/x",))
        assert strip_target_for("tooling", _LISTING) == StripTarget("tooling", ("main",))

    def test_strip_target_for_a_project_without_rows_carries_no_branch(self) -> None:
        listing = WorkspaceBranchListing(projects={"demo": ()})
        assert strip_target_for("demo", listing) == StripTarget("demo", ())

    def test_strip_cells_collapses_a_repeated_cell(self) -> None:
        targets = (_target("tooling", "main"), _target("tooling", "main"))
        assert strip_cells(targets) == (ScopeCell("tooling", "main"),)

    def test_ordered_targets_puts_unknown_projects_last(self) -> None:
        targets = (_target("frontend", "main"), _target("backend", "main"))
        assert ordered_targets(targets, _REVERSED) == targets[::-1]

    def test_missing_cells_name_only_what_the_listing_lacks(self) -> None:
        state = StripState(
            (StripTarget("backend", ("feature/x", "gone")), _target("frontend", "main"))
        )
        assert missing_strip_cells(state, _LISTING) == (
            ScopeCell("backend", "gone"),
            ScopeCell("frontend", "main"),
        )

    def test_nothing_is_missing_from_the_listing_it_was_built_from(self) -> None:
        state = StripState(
            (strip_target_for("backend", _LISTING), strip_target_for("tooling", _LISTING))
        )
        assert missing_strip_cells(state, _LISTING) == ()

    def test_chip_label_names_the_cell(self) -> None:
        assert strip_chip_label(ScopeCell("backend", "main")) == "backend · main ✕"
        assert strip_chip_label(ScopeCell("backend", "")) == "backend ✕"  # E8


def test_a_pinned_strip_carries_no_default_only_fields() -> None:
    """branch_default / branch_name are DEFAULT-only (spec §6.2): a PIN leaves them
    at the value object's own defaults, whatever YAML holds."""
    config = ScopeDefaultsConfig(branch_default=ScopeBranchDefault.CHECKED_OUT, branch_name="main")
    scope = compile_strip_scope((_target("tooling", "main"),), True, config, _LISTING)
    untouched = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", "main"),))
    assert (scope.branch_default, scope.branch_name) == (
        untouched.branch_default,
        untouched.branch_name,
    )


def test_the_compiler_never_sees_a_capability_it_was_not_given() -> None:
    """The U0 default: no capability record means no branch argument anywhere."""
    scope = compile_strip_scope(
        (_target("backend", "main"),), False, _CONFIG, _LISTING, capabilities=NO_SCOPE_CAPABILITIES
    )
    assert scope.branch_name == ""


def test_strip_state_module_stays_streamlit_and_langchain_free() -> None:
    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.strip_state\n"
        "assert not any(m.startswith(('langchain', 'streamlit')) for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith(('langchain', 'streamlit')))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "python")}
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


# --- AC-35's cap clause: the strip's cap is the interceptor's cap -------------


def test_ac35_five_target_strip_is_refused_before_any_call() -> None:
    """Five compiled cells against max_cells 4: refused before any call, zero handler calls."""
    from pydocs_mcp.harness.ask_your_docs.scope_interceptor import ScopeRuntime
    from tests.harness.ask_your_docs.test_scope_interceptor import RecordingHandler, active, call

    listing = WorkspaceBranchListing(projects={p: (_row("main", default=True),) for p in "abcde"})
    targets = tuple(strip_target_for(p, listing) for p in "abcde")
    scope = compile_strip_scope(targets, False, _CONFIG, listing)
    assert scope.kind is ScopeKind.PIN
    assert len(scope.cells) == 5
    handler = RecordingHandler()
    runtime = ScopeRuntime(listing=listing, capabilities=NO_SCOPE_CAPABILITIES, max_cells=4)
    with active(scope, runtime):
        result = call("get_overview", {}, handler)
    assert handler.sent == []
    assert result.isError is True
    assert "ask_your_docs.scope.max_cells" in result.content[0].text
