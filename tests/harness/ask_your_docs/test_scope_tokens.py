"""``scope_tokens`` — AC-36, AC-37, AC-38, AC-39 (parser half), E13–E15 (spec §6.10a).

Fixture rule (spec §11): the tokened project is ``tooling`` — neither the YAML default
project (``any``) nor the listing's first row — so "tokens win", "the default was
already that" and "the first project wins" are three different answers. The two
projects also disagree on default vs base row (backend is stamped on ``feature/retry``
with base ``main``; tooling is stamped on ``main``), so a cell that carries the base,
the first row or an empty branch is told apart from one that carries the stamped row.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_tokens import (
    BRANCH_TOKEN_PREFIX,
    BRANCHES_NOT_CHOOSABLE,
    PROJECT_TOKEN_PREFIX,
    ParsedScopeTokens,
    parse_scope_tokens,
    strip_scope_tokens,
)
from pydocs_mcp.models import BranchStatus

_REPO_ROOT = Path(__file__).resolve().parents[3]

U1 = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


def _row(name: str, *, default: bool = False, base: str | None = None) -> IndexedBranch:
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


def _merged_row(name: str) -> IndexedBranch:
    return IndexedBranch(name, "b" * 40, "main", False, BranchStatus.MERGED, "c" * 40, None, 1.0)


# Insertion order is NOT alphabetical on purpose: the refusals list the indexed names
# in listing order, so a parser that sorts them fails.
LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/retry", default=True, base="main"), _row("main")),
        "tooling": (
            _row("main", default=True),
            _row("develop", base="main"),
            _merged_row("release/1.0"),
        ),
        "example_needle": (_row("main", default=True),),
    }
)
ONE_PROJECT = WorkspaceBranchListing(projects={"backend": LISTING.rows("backend")})
# E8: a pre-v16 bundle contributes a project with no branch row at all.
NO_ROWS = WorkspaceBranchListing(projects={"demo": ()})


def _parse(
    text: str,
    *,
    listing: WorkspaceBranchListing = LISTING,
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
    strip: tuple[str, ...] = (),
    enabled: bool = True,
    cap: int = 4,
) -> ParsedScopeTokens:
    return parse_scope_tokens(
        text, listing, capabilities, strip, tokens_enabled=enabled, max_cells=cap
    )


class TestGrammar:  # AC-36
    def test_in_token_names_a_project_and_is_stripped(self) -> None:
        parsed = _parse("why does retry drop the last attempt? in:tooling")
        assert parsed == ParsedScopeTokens(
            (ScopeCell("tooling", "main"),), "why does retry drop the last attempt?"
        )

    def test_a_token_cell_carries_the_stamped_row_not_the_base_or_an_empty_branch(self) -> None:
        """One U0 cell shape, every source (spec §6.4a): the listing's default row."""
        assert _parse("q in:backend").cells == (ScopeCell("backend", "feature/retry"),)

    def test_tokens_anywhere_keep_question_order_and_collapse_their_whitespace(self) -> None:
        parsed = _parse("in:tooling  what   in:backend is Foo?")
        assert parsed.cells == (
            ScopeCell("tooling", "main"),
            ScopeCell("backend", "feature/retry"),
        )
        assert parsed.stripped_text == "what is Foo?"

    def test_a_multi_line_question_keeps_its_newlines_and_inner_whitespace(self) -> None:
        """Spec §6.10a removes a token with the whitespace AROUND IT collapsed; every
        other byte survives, so a re-join of the whole text on single spaces is a bug."""
        parsed = _parse("line one\nline  two in:tooling\nline three")
        assert parsed.cells == (ScopeCell("tooling", "main"),)
        assert parsed.stripped_text == "line one\nline  two\nline three"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("in:tooling what is Foo?", "what is Foo?"),
            ("what is in:tooling Foo?", "what is Foo?"),
            ("what is Foo? in:tooling", "what is Foo?"),
        ],
    )
    def test_a_token_leaves_no_doubled_space_at_any_position(
        self, text: str, expected: str
    ) -> None:
        assert _parse(text).stripped_text == expected

    def test_a_text_with_no_token_comes_back_as_the_same_object(self) -> None:
        text = "why  is\nFoo like this?"
        assert strip_scope_tokens(text) is text

    def test_a_repeated_project_yields_one_cell(self) -> None:
        assert _parse("in:tooling q in:tooling").cells == (ScopeCell("tooling", "main"),)

    def test_a_bare_prefix_is_question_text_not_a_token(self) -> None:
        parsed = _parse("what does in: mean on: this page?")
        assert parsed.cells == ()
        assert parsed.stripped_text == "what does in: mean on: this page?"
        assert parsed.refusal == ""

    def test_no_token_text_is_byte_identical(self) -> None:
        # Doubled spaces on purpose: a stripper that always re-joins would collapse them.
        text = "what  is   Foo?"
        parsed = _parse(text)
        assert parsed.cells == () and parsed.stripped_text == text and parsed.refusal == ""
        assert strip_scope_tokens(text) == text

    def test_tokens_disabled_parses_nothing_and_refuses_nothing(self) -> None:
        text = "q in:backnd on:nope"
        assert _parse(text, enabled=False) == ParsedScopeTokens((), text)

    def test_names_are_matched_case_sensitively(self) -> None:
        """Project and branch names are file-system / git names (spec §6.10a)."""
        assert _parse("q in:Tooling").refusal.startswith("No project named 'Tooling'")
        assert _parse("q in:tooling on:Develop", capabilities=U1).refusal.startswith(
            "No branch named 'Develop' on tooling"
        )

    def test_a_project_with_no_branch_row_yields_an_empty_branch_cell(self) -> None:
        """E8 is the ONLY empty-branch cell shape (spec §6.4a)."""
        assert _parse("q in:demo", listing=NO_ROWS).cells == (ScopeCell("demo", ""),)

    def test_on_attaches_to_the_nearest_preceding_in(self) -> None:
        parsed = _parse("q in:backend on:main in:tooling", capabilities=U1)
        # tooling had no on: → its DEFAULT row (main), not the base and not backend's branch
        assert parsed.cells == (ScopeCell("backend", "main"), ScopeCell("tooling", "main"))
        assert parsed.stripped_text == "q"

    def test_a_later_in_moves_the_attachment_point(self) -> None:
        """Nearest preceding, not the first: a second ``in:`` owns every ``on:`` after it,
        and the first project keeps its own stamped row."""
        parsed = _parse("in:backend in:tooling on:main q", capabilities=U1)
        assert parsed.cells == (
            ScopeCell("backend", "feature/retry"),
            ScopeCell("tooling", "main"),
        )
        # develop exists on tooling only: attaching it to the FIRST in: would refuse.
        assert _parse("in:backend in:tooling on:develop", capabilities=U1).cells == (
            ScopeCell("backend", "feature/retry"),
            ScopeCell("tooling", "develop"),
        )

    def test_several_on_after_one_in_select_several_branches(self) -> None:
        parsed = _parse("in:tooling on:develop on:main q", capabilities=U1)
        assert parsed.cells == (ScopeCell("tooling", "develop"), ScopeCell("tooling", "main"))

    def test_a_repeated_branch_yields_one_cell(self) -> None:
        parsed = _parse("in:tooling on:develop q on:develop", capabilities=U1)
        assert parsed.cells == (ScopeCell("tooling", "develop"),)

    def test_lone_on_with_one_project_in_play_resolves(self) -> None:
        by_strip = _parse("q on:develop", capabilities=U1, strip=("tooling",))
        assert by_strip.cells == (ScopeCell("tooling", "develop"),)
        by_workspace = _parse("q on:main", listing=ONE_PROJECT, capabilities=U1)
        assert by_workspace.cells == (ScopeCell("backend", "main"),)

    @pytest.mark.parametrize("glued", ["?", ".", ",", ";", ":", "!", ")"])
    def test_punctuation_glued_to_a_token_is_not_part_of_the_name(self, glued: str) -> None:
        """Spec §6.10a / P24: `in:tooling?` names tooling; the whole word (punctuation
        included) leaves the sent text, so the question ends without its `?`."""
        parsed = _parse(f"what is Foo in:tooling{glued}")
        assert parsed.cells == (ScopeCell("tooling", "main"),)
        assert parsed.stripped_text == "what is Foo"

    def test_punctuation_is_stripped_from_a_branch_name_too(self) -> None:
        parsed = _parse("q in:backend on:main!", capabilities=U1)
        assert parsed.cells == (ScopeCell("backend", "main"),) and parsed.refusal == ""

    def test_a_prefix_followed_by_punctuation_only_is_question_text(self) -> None:
        parsed = _parse("what does in:? mean")
        assert parsed.cells == () and parsed.stripped_text == "what does in:? mean"

    def test_a_bundle_stem_is_normalized_to_its_project_name(self) -> None:
        """Spec §6.10a: a stem is accepted (as in §6.3) and normalized at parse time, so
        no stem ever reaches a cell, a chip or a caption."""
        stems = WorkspaceBranchListing(
            projects=LISTING.projects, bundle_stems=frozenset({"tooling_0123456789"})
        )
        parsed = parse_scope_tokens(
            "q in:tooling_0123456789",
            stems,
            NO_SCOPE_CAPABILITIES,
            (),
            tokens_enabled=True,
            max_cells=4,
        )
        assert parsed.cells == (ScopeCell("tooling", "main"),) and parsed.refusal == ""

    def test_a_renamed_bundles_stem_still_names_its_project(self) -> None:
        """The listing owns the stem -> project map (§6.10a), read from each bundle's own
        stamp — so a hand-renamed file resolves even though its stem shares no prefix."""
        renamed = WorkspaceBranchListing(
            projects=LISTING.projects,
            bundle_stems=frozenset({"archive-2026"}),
            stem_projects={"archive-2026": "tooling"},
        )
        parsed = _parse("q in:archive-2026", listing=renamed)
        assert parsed.cells == (ScopeCell("tooling", "main"),) and parsed.refusal == ""

    def test_stems_of_two_projects_that_share_a_prefix_resolve_apart(self) -> None:
        pair = WorkspaceBranchListing(
            projects={
                "api": (_row("main", default=True),),
                "api_v2": (_row("trunk", default=True),),
            },
            bundle_stems=frozenset({"api_0123456789", "api_v2_0123456789"}),
            stem_projects={"api_0123456789": "api", "api_v2_0123456789": "api_v2"},
        )
        assert _parse("q in:api_v2_0123456789", listing=pair).cells == (
            ScopeCell("api_v2", "trunk"),
        )
        assert _parse("q in:api_0123456789", listing=pair).cells == (ScopeCell("api", "main"),)

    def test_an_unknown_stem_is_refused_with_the_indexed_project_names(self) -> None:
        stems = WorkspaceBranchListing(
            projects=LISTING.projects,
            bundle_stems=frozenset({"tooling_0123456789"}),
            stem_projects={"tooling_0123456789": "tooling"},
        )
        assert _parse("q in:tooling_nope", listing=stems).refusal == (
            "No project named 'tooling_nope'. Indexed: backend, tooling, example_needle. "
            "Nothing was sent."
        )

    def test_strip_scope_tokens_is_syntactic_and_needs_no_listing(self) -> None:
        assert strip_scope_tokens("q in:nope on:whatever") == "q"

    def test_prefix_constants_are_the_only_source_of_the_literals(self) -> None:
        import pydocs_mcp.harness.ask_your_docs.scope_tokens as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert (PROJECT_TOKEN_PREFIX, BRANCH_TOKEN_PREFIX) == ("in:", "on:")
        assert source.count('"in:"') == 1 and source.count('"on:"') == 1


class TestRefusals:  # AC-37, AC-38, E13, E14, E15, E4
    def test_unknown_project_refuses_with_the_indexed_names(self) -> None:
        parsed = _parse("what is Foo? in:backnd")
        assert parsed.refusal == (
            "No project named 'backnd'. Indexed: backend, tooling, example_needle. "
            "Nothing was sent."
        )
        assert parsed.cells == () and parsed.stripped_text == "what is Foo? in:backnd"

    def test_on_is_refused_before_branch_selector_even_for_the_stamped_branch(self) -> None:
        # main IS tooling's stamped branch: a parser that lets the stamped name through fails.
        assert _parse("q in:tooling on:main").refusal == BRANCHES_NOT_CHOOSABLE
        # Refused BEFORE the project name is checked (§6.10a).
        assert _parse("q in:backnd on:main").refusal == BRANCHES_NOT_CHOOSABLE
        assert BRANCHES_NOT_CHOOSABLE == (
            "Branches can't be chosen yet: this server indexes one branch per project."
        )

    def test_a_refused_question_keeps_its_text_and_yields_no_cell(self) -> None:
        parsed = _parse("q in:tooling on:main")
        assert parsed.cells == () and parsed.stripped_text == "q in:tooling on:main"

    def test_unknown_branch_of_a_known_project(self) -> None:
        parsed = _parse("q in:backend on:featur/retry", capabilities=U1)
        assert parsed.refusal == (
            "No branch named 'featur/retry' on backend. Indexed: feature/retry, main. "
            "Nothing was sent."
        )

    def test_a_merged_branch_is_picker_only_and_cannot_be_typed(self) -> None:
        """`on:` matches `listing.pickable`, so a landed row is neither accepted nor listed."""
        parsed = _parse("q in:tooling on:release/1.0", capabilities=U1)
        assert parsed.refusal == (
            "No branch named 'release/1.0' on tooling. Indexed: main, develop. Nothing was sent."
        )

    def test_lone_on_with_two_projects_in_play_is_refused(self) -> None:
        parsed = _parse("q on:main", capabilities=U1)
        assert parsed.refusal == (
            "on:main needs a project: add in:<project> before it. "
            "In play: backend, tooling, example_needle. Nothing was sent."
        )
        two = _parse("q on:main", capabilities=U1, strip=("backend", "tooling"))
        assert two.refusal.startswith("on:main needs a project")
        assert "In play: backend, tooling." in two.refusal

    def test_a_bare_on_before_an_in_is_refused_and_names_the_order(self) -> None:
        """P24: the ordering is the mistake, so the message says so — and a later `in:`
        wins over the lone-project rule, because it says which project was meant."""
        parsed = _parse("q on:main in:backend", capabilities=U1)
        assert parsed.refusal == (
            "on:main must come after its in:<project> (found in:backend later in the question). "
            "Nothing was sent."
        )
        with_lone = _parse("q on:develop in:backend", capabilities=U1, strip=("tooling",))
        assert with_lone.refusal.startswith("on:develop must come after its in:<project>")
        assert with_lone.cells == ()

    def test_an_empty_workspace_refuses_with_words_not_a_dangling_list(self) -> None:
        """A bare ``Indexed: .`` / ``In play: .`` reads as a rendering bug on screen."""
        empty = WorkspaceBranchListing(projects={})
        assert _parse("q in:backend", listing=empty).refusal == (
            "No project named 'backend'. No projects are indexed. Nothing was sent."
        )
        assert _parse("q on:main", listing=empty, capabilities=U1).refusal == (
            "on:main needs a project: add in:<project> before it. "
            "No projects are indexed. Nothing was sent."
        )

    def test_a_project_with_no_branch_row_refuses_an_on_token_with_words(self) -> None:
        assert _parse("q in:demo on:main", listing=NO_ROWS, capabilities=U1).refusal == (
            "No branch named 'main' on demo. No branches are indexed for demo. Nothing was sent."
        )

    def test_over_the_cap_is_refused_before_any_call(self) -> None:
        many = WorkspaceBranchListing(projects={p: (_row("main", default=True),) for p in "abcde"})
        parsed = _parse("q in:a in:b in:c in:d in:e", listing=many, cap=4)
        assert parsed.cells == ()
        assert parsed.refusal == (
            "That would be 5 searches; the limit is 4 (ask_your_docs.scope.max_cells). "
            "Nothing was sent."
        )
        assert _parse("q in:a in:b in:c in:d", listing=many, cap=4).refusal == ""


def test_scope_tokens_module_stays_streamlit_and_langchain_free() -> None:
    code = (
        "import sys\n"
        "import pydocs_mcp.harness.ask_your_docs.scope_tokens\n"
        "assert not any(m.startswith(('langchain', 'streamlit')) for m in sys.modules), "
        "sorted(m for m in sys.modules if m.startswith(('langchain', 'streamlit')))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "python")}
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
