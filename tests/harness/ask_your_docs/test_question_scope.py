"""QuestionScope invariants, default resolution, prefix — AC-23, 28, 29, 30, 33."""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol, weave_attachments
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    MODEL_NOTE_SLICE_WORDS,
    SLICE_LABELS,
    QuestionScope,
    ScopeBranchDefault,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
    listing_cell,
    pin_or_none,
    pin_with_attached_symbols,
    resolve_default_branch,
    resolve_question_scope_defaults,
    scope_caption_text,
    scope_prefix,
    snapshot_pin_for_send,
    token_scope,
)
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig


def _row(name, *, default=False, base=None):
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


_LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/x", default=True, base="main"), _row("main")),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789"}),
)
_PIN = QuestionScope(
    kind=ScopeKind.PIN,
    cells=(ScopeCell("backend", "main"), ScopeCell("backend", "feature/retry")),
    slice=ScopeSlice.DIFF_HUNKS,
    code=ScopeCode.OWN,
)


class TestInvariants:  # AC-23
    def test_default_needs_exactly_one_empty_branch_cell(self):
        with pytest.raises(ValueError, match="DEFAULT scope holds exactly one cell"):
            QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("a", ""), ScopeCell("b", "")))
        with pytest.raises(ValueError, match="empty branch"):
            QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("p", "main"),))

    def test_cells_must_be_non_empty_and_unique(self):
        with pytest.raises(ValueError, match="cells is empty"):
            QuestionScope(kind=ScopeKind.PIN, cells=())
        with pytest.raises(ValueError, match="duplicates"):
            QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("p", "m"), ScopeCell("p", "m")))

    def test_slice_excludes_dependencies_only(self):
        with pytest.raises(ValueError) as excinfo:
            QuestionScope(
                kind=ScopeKind.PIN,
                cells=(ScopeCell("p", "m"),),
                slice=ScopeSlice.DIFF_HUNKS,
                code=ScopeCode.DEPS,
            )
        assert "diff_hunks" in str(excinfo.value) and "deps" in str(excinfo.value)

    def test_with_and_without_cells(self):
        grown = _PIN.with_cells((ScopeCell("backend", "main"), ScopeCell("tooling", "main")))
        assert grown.cells == (*_PIN.cells, ScopeCell("tooling", "main"))
        assert _PIN.with_cells(_PIN.cells) is _PIN
        one = _PIN.without_cell(ScopeCell("backend", "feature/retry"))
        assert one.cells == (ScopeCell("backend", "main"),)
        assert one.without_cell(ScopeCell("backend", "main")) is None


class TestPrefix:  # AC-28
    def test_two_branch_pin_renders_every_element(self):
        assert scope_prefix(_PIN) == (
            "[pinned scope: project=backend, branches=main, feature/retry, diff hunks, own code only] "
        )

    def test_default_renders_nothing(self):
        assert scope_prefix(QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))) == ""
        assert scope_prefix(None) == ""

    def test_one_cell_pin_without_branch_is_todays_bytes(self):
        pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", ""),))
        assert scope_prefix(pin) == "[pinned scope: project=backend] "

    def test_caption_reads_searched_in(self):
        """AC-39 (caption half): the transcript caption names the cells, pipe-separated
        per project, in the D14 on-screen words."""
        assert scope_caption_text(_PIN) == (
            "searched in: backend · main, feature/retry · only the changes themselves"
        )
        two = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("a", "main"), ScopeCell("b", "main"))
        )
        assert scope_caption_text(two) == "searched in: a · main | b · main"
        assert scope_caption_text(two, from_question=True) == (
            "searched in: a · main | b · main (from your question)"
        )
        bare = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("tooling", ""),))
        assert scope_caption_text(bare, from_question=True) == (
            "searched in: tooling (from your question)"
        )
        assert scope_caption_text(None) == ""
        assert (
            scope_caption_text(QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),)))
            == ""
        )

    def test_model_note_bytes_do_not_follow_the_screen_words(self):
        """AC-28 stays green through the split: the screen tables change, the note's do not."""
        assert scope_prefix(_PIN) == (
            "[pinned scope: project=backend, branches=main, feature/retry, diff hunks, own code only] "
        )
        assert MODEL_NOTE_SLICE_WORDS[ScopeSlice.DIFF_HUNKS] == "diff hunks"
        assert SLICE_LABELS[ScopeSlice.DIFF_HUNKS] == "only the changes themselves"


class TestResolveDefaultBranch:  # AC-29
    def _scope(self, **kwargs):
        return QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("backend", ""),), **kwargs)

    def test_base_resolves_to_the_default_rows_base_when_listed_and_different(self):
        assert resolve_default_branch(self._scope(), "backend", _LISTING) == "main"
        assert resolve_default_branch(self._scope(), "tooling", _LISTING) == ""

    def test_checked_out_sends_nothing(self):
        scope = self._scope(branch_default=ScopeBranchDefault.CHECKED_OUT)
        assert resolve_default_branch(scope, "backend", _LISTING) == ""

    def test_named_default_wins_when_listed(self):
        assert (
            resolve_default_branch(self._scope(branch_name="main"), "backend", _LISTING) == "main"
        )

    def test_unlisted_name_resolves_to_nothing_and_logs(self, caplog):
        with caplog.at_level("INFO"):
            got = resolve_default_branch(self._scope(branch_name="gone"), "backend", _LISTING)
        assert got == ""
        record = json.loads(caplog.records[-1].getMessage())
        assert record == {
            "argument": "branch_name",
            "event": "scope_default_replaced",
            "passed": "gone",
            "replacement": "",
            "tool": "",
        }

    def test_union_project_sends_nothing(self):
        assert resolve_default_branch(self._scope(), "", _LISTING) == ""

    def test_p0_bundle_without_base_name_sends_nothing(self):
        listing = WorkspaceBranchListing(projects={"backend": (_row("main", default=True),)})
        assert resolve_default_branch(self._scope(), "backend", listing) == ""


class TestResolveDefaults:  # AC-33 layering
    def test_shipped_config_and_empty_override_give_the_union_cell(self):
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        assert scope == QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))

    def test_session_override_wins_over_yaml(self):
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(code=ScopeCode.OWN),
            ScopeDefaultsOverride(project="backend", code=ScopeCode.ALL, package="fastapi"),
            _LISTING,
        )
        assert scope.cells == (ScopeCell("backend", ""),)
        assert scope.code is ScopeCode.ALL and scope.package == "fastapi"

    def test_unknown_project_default_falls_back_to_the_union(self, caplog):
        with caplog.at_level("INFO"):
            scope = resolve_question_scope_defaults(
                ScopeDefaultsConfig(project="gone"), ScopeDefaultsOverride(), _LISTING
            )
        assert scope.cells == (ScopeCell("", ""),)
        assert "scope_default_replaced" in caplog.records[-1].getMessage()

    def test_empty_listing_keeps_a_named_project(self):
        # Nothing scanned yet (CLI / tests): the name passes through unchecked.
        scope = resolve_question_scope_defaults(
            ScopeDefaultsConfig(project="backend"), ScopeDefaultsOverride(), EMPTY_BRANCH_LISTING
        )
        assert scope.cells == (ScopeCell("backend", ""),)


class TestAttachedSymbols:  # AC-30
    def test_attaching_with_no_pin_creates_a_one_shot_cell_pin(self):
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        attached = [AttachedSymbol("mod.Foo", "backend", "feature/retry")]
        pin = pin_with_attached_symbols(None, attached, defaults)
        assert pin.kind is ScopeKind.PIN and pin.cells == (ScopeCell("backend", "feature/retry"),)
        assert weave_attachments(attached, "what is it?") == "Regarding `mod.Foo`: what is it?"

    def test_attaching_under_a_pin_adds_the_cell_once(self):
        attached = [
            AttachedSymbol("mod.Foo", "backend", "main"),
            AttachedSymbol("mod.Bar", "tooling", "main"),
        ]
        pin = pin_with_attached_symbols(_PIN, attached, _PIN)
        assert pin.cells == (*_PIN.cells, ScopeCell("tooling", "main"))

    def test_plain_string_attachments_still_weave(self):
        assert weave_attachments(["a.B", "a.B", ""], "q") == "Regarding `a.B`: q"

    def test_snapshot_sends_the_active_scope_grown_by_attached_cells_only(self):
        """AC-30 (D14): the strip is sticky, so a snapshot returns ONE scope — the active
        one, grown by attached cells for this send only. An attached cell separates
        "grew" from "passed through"."""
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        assert snapshot_pin_for_send(defaults, []) == defaults
        assert snapshot_pin_for_send(_PIN, []) == _PIN
        attached = [AttachedSymbol("mod.Foo", "tooling", "main")]
        sent = snapshot_pin_for_send(defaults, attached)
        assert sent.kind is ScopeKind.PIN and sent.cells == (ScopeCell("tooling", "main"),)
        assert snapshot_pin_for_send(_PIN, attached).cells == (
            *_PIN.cells,
            ScopeCell("tooling", "main"),
        )

    def test_a_branchless_attachment_takes_the_listings_stamped_branch(self):
        """§6.4a: one U0 cell shape from every source — a graph attach that carries no
        branch must not mint (backend, "") beside the strip's (backend, feature/x)."""
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        attached = [AttachedSymbol("mod.Foo", "backend", "")]
        sent = snapshot_pin_for_send(defaults, attached, _LISTING)
        assert sent.cells == (ScopeCell("backend", "feature/x"),)
        # Without a listing the caller keeps today's shape: no rows, no branch.
        assert snapshot_pin_for_send(defaults, attached).cells == (ScopeCell("backend", ""),)

    def test_pin_or_none_answers_is_a_pin_active(self):
        defaults = resolve_question_scope_defaults(
            ScopeDefaultsConfig(), ScopeDefaultsOverride(), _LISTING
        )
        assert pin_or_none(_PIN) is _PIN
        assert pin_or_none(defaults) is None

    def test_token_cells_become_a_one_shot_pin_carrying_the_more_values(self):
        """AC-40 (pure half): code / package ride from the active scope; the slice is
        ALWAYS the whole branch (owner decision D14 §4, spec §6.10a) — an active diff
        slice must not leak into a typed `in:` question."""
        active = QuestionScope(
            kind=ScopeKind.DEFAULT,
            cells=(ScopeCell("", ""),),
            code=ScopeCode.OWN,
            package="fastapi",
        )
        scope = token_scope((ScopeCell("tooling", ""),), active)
        assert scope == QuestionScope(
            kind=ScopeKind.PIN,
            cells=(ScopeCell("tooling", ""),),
            code=ScopeCode.OWN,
            package="fastapi",
        )
        sliced = QuestionScope(
            kind=ScopeKind.DEFAULT,
            cells=(ScopeCell("", ""),),
            slice=ScopeSlice.DIFF_HUNKS,
            code=ScopeCode.OWN,
        )
        assert token_scope((ScopeCell("tooling", ""),), sliced).slice is ScopeSlice.WHOLE_BRANCH


class TestListingCell:  # spec §6.4a — one U0 cell shape, every source
    def test_a_named_branch_is_kept_as_typed(self):
        assert listing_cell(_LISTING, "backend", "main") == ScopeCell("backend", "main")

    def test_an_empty_branch_fills_the_default_row_not_the_base(self):
        # backend's default row is feature/x, whose base is main: a cell carrying the
        # base, the first-by-name row or "" is a DIFFERENT cell to the value object.
        assert listing_cell(_LISTING, "backend") == ScopeCell("backend", "feature/x")

    def test_a_project_with_no_branch_row_keeps_an_empty_branch(self):
        """E8 — the only empty-branch cell shape (a pre-v16 bundle)."""
        listing = WorkspaceBranchListing(projects={"demo": ()})
        assert listing_cell(listing, "demo") == ScopeCell("demo", "")
