"""QuestionScope invariants, default resolution, prefix — AC-23, 28, 29, 30, 33."""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol, weave_attachments
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
    pin_summary_label,
    pin_with_attached_symbols,
    resolve_default_branch,
    resolve_question_scope_defaults,
    scope_caption_text,
    scope_prefix,
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

    def test_caption_and_summary(self):
        assert scope_caption_text(_PIN) == "backend · main, feature/retry · diff hunks"
        assert pin_summary_label(_PIN) == "backend · 2 branches"
        two = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("a", "main"), ScopeCell("b", "main"))
        )
        assert pin_summary_label(two) == "2 projects"
        one = QuestionScope(
            kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),), slice=ScopeSlice.DIFF_HUNKS
        )
        assert pin_summary_label(one) == "backend · main · diff hunks"
        assert pin_summary_label(None) == ""


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
