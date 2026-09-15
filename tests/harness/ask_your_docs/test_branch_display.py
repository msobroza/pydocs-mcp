"""The non-git sentinel row never reaches the screen as a branch called "no git".

A project indexed outside a git repository is stamped with models.NON_GIT_BRANCH_NAME
so the engine keeps one cell per project; every display site — the picker caption, the
strip chip, the transcript caption, the footer segment — shows it as "no branch".
"""

from __future__ import annotations

from pydocs_mcp.harness.ask_your_docs.answer_footer import render_answer_footer
from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeKind,
    branch_for_display,
    scope_caption_text,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.harness.ask_your_docs.scope_panel import INDEXED_WITHOUT_GIT, branch_caption
from pydocs_mcp.harness.ask_your_docs.strip_state import strip_chip_label
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeSlice

# The stamped row of a project indexed outside git: the sentinel name, no sha.
_NON_GIT_ROW = IndexedBranch(
    NON_GIT_BRANCH_NAME, "", None, True, BranchStatus.ACTIVE, None, None, 1.0
)
_GIT_ROW = IndexedBranch("feature/x", "a" * 40, "main", True, BranchStatus.ACTIVE, None, None, 1.0)
_LISTING = WorkspaceBranchListing(projects={"playbook": (_NON_GIT_ROW,), "backend": (_GIT_ROW,)})


def test_branch_for_display_hides_only_the_sentinel() -> None:
    assert branch_for_display(NON_GIT_BRANCH_NAME) == ""
    assert branch_for_display("feature/x") == "feature/x"
    assert branch_for_display("") == ""


def test_picker_caption_says_indexed_without_git() -> None:
    assert branch_caption("playbook", _LISTING) == INDEXED_WITHOUT_GIT
    assert branch_caption("backend", _LISTING) == "indexed on feature/x @aaaaaaa"


def test_strip_chip_shows_the_project_alone_for_a_non_git_target() -> None:
    assert strip_chip_label(ScopeCell("playbook", NON_GIT_BRANCH_NAME)) == "playbook ✕"
    assert strip_chip_label(ScopeCell("backend", "feature/x")) == "backend · feature/x ✕"


def test_transcript_caption_drops_the_sentinel_branch() -> None:
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("backend", "feature/x"), ScopeCell("playbook", NON_GIT_BRANCH_NAME)),
    )
    assert scope_caption_text(pin) == "searched in: backend · feature/x | playbook"


def test_footer_segment_reads_no_branch_for_a_non_git_cell() -> None:
    record = CellObservation(
        tool="search_codebase",
        project="playbook",
        branch=NON_GIT_BRANCH_NAME,
        branch_origin=BranchOrigin.PINNED,
        slice=ScopeSlice.WHOLE_BRANCH,
        meta={"branch": NON_GIT_BRANCH_NAME, "index_stale": False},
        replaced=False,
    )
    observations = ScopeObservations()
    observations.append(record)
    footer = render_answer_footer(observations, _LISTING)
    assert footer.startswith("Searched playbook · no branch")
    assert NON_GIT_BRANCH_NAME not in footer
