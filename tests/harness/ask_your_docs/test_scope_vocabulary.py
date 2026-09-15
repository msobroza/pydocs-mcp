"""AC-47, source-text half: the retired segments never reach a screen string literal; the
model note keeps its bytes. Core deps only — the rendered-tree half is
test_app_scope_vocabulary.py (AppTest).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pydocs_mcp.harness.ask_your_docs.question_scope import (
    MODEL_NOTE_CODE_WORDS,
    MODEL_NOTE_SLICE_WORDS,
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
    scope_prefix,
)

_HARNESS = Path(__file__).resolve().parents[3] / "python/pydocs_mcp/harness/ask_your_docs"
# AC-47 names scope_panel.py, activity_labels.py and scope_pin.py; scope_strip.py,
# scope_picker.py, answer_footer.py, page_send.py, app.py and pages/2_Graph.py are the
# D14 modules that inherited scope_panel.py's screen strings.
_SCREEN_MODULES = (
    "scope_panel.py",
    "activity_labels.py",
    "scope_pin.py",
    "scope_strip.py",
    "scope_picker.py",
    "answer_footer.py",
    "page_send.py",
    "app.py",
    "pages/2_Graph.py",
)
# AC-47's enumerated segments, verbatim and in its order. Each entry carries its OWN
# delimiters, and that is what makes this a whole-segment sweep rather than a substring
# one: "your default", "the server's default" and "main (base branch)" all contain the
# word "default" and must pass, because the retired spellings are "· default" and
# "(default)". `(default)` is retired only OUTSIDE the model-facing catalog line, which
# lives in catalog.py — not a screen module, so it is out of this tuple's reach by
# construction and needs no exemption here.
_RETIRED = (
    "Scope defaults",
    "keep for next",
    "Reset to shipped",
    "whole branch",
    "own code only",
    "all code",
    "answered from",
    "index stale",
    "(default)",
    "· default",
    "(pinned)",
    "· pinned",
    "pinned by you",
    "agent-chosen",
    "server default",
)
_RETIRED_BUTTON_LABEL = "Pin"  # a LABEL equality, never a substring: "Pinned to" is not it


def _docstring_ids(tree: ast.Module) -> set[int]:
    """The id() of every node that IS a docstring — prose, never on screen (AC-47)."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, holders)
        and ast.get_docstring(node, clean=False) is not None
        and isinstance(node.body[0], ast.Expr)
    }


def _screen_literals(path: Path) -> list[str]:
    """Every string literal in ``path`` except docstrings.

    Parsed rather than grepped: a docstring or a comment that names a retired word is
    explaining the rule, not showing it to anyone, and only literals can reach a widget.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


@pytest.mark.parametrize("name", _SCREEN_MODULES)
def test_no_retired_segment_in_screen_string_literals(name: str) -> None:
    literals = _screen_literals(_HARNESS / name)
    assert not [s for s in literals if any(segment in s for segment in _RETIRED)], name
    assert _RETIRED_BUTTON_LABEL not in [s.strip("✕ ") for s in literals], name


def test_the_three_label_tables_are_reworded_but_the_note_is_not() -> None:
    """The exemption is BY NAME: the two MODEL_NOTE_* tables keep the 2026-09-04 words.
    AC-47's third table is answer_footer's ORIGIN_LABELS, asserted here too — its values
    carry the word "default" and are exactly the approved phrases that must pass."""
    from pydocs_mcp.harness.ask_your_docs.answer_footer import ORIGIN_LABELS
    from pydocs_mcp.harness.ask_your_docs.question_scope import CODE_LABELS, SLICE_LABELS

    assert "whole branch" not in SLICE_LABELS.values()
    assert "own code only" not in CODE_LABELS.values()
    assert not [v for v in ORIGIN_LABELS.values() if any(seg in v for seg in _RETIRED)]
    assert set(ORIGIN_LABELS.values()) == {
        "your default",
        "only these",
        "the agent's choice",
        "the server's default",
    }
    assert MODEL_NOTE_SLICE_WORDS[ScopeSlice.WHOLE_BRANCH] == "whole branch"
    assert MODEL_NOTE_CODE_WORDS[ScopeCode.OWN] == "own code only"
    pin = QuestionScope(
        kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),), code=ScopeCode.OWN
    )
    assert scope_prefix(pin) == "[pinned scope: project=backend, branch=main, own code only] "


def test_the_activity_panels_corpus_and_code_words_are_the_screen_vocabulary() -> None:
    """The activity panel is on screen (§6.7 words), so the two tables it reads take the
    picker's Code wording — while ``MODEL_NOTE_CODE_WORDS`` keeps the model's bytes."""
    from pydocs_mcp.harness.ask_your_docs.activity_labels import scope_note, tool_step_label
    from pydocs_mcp.harness.ask_your_docs.question_scope import CODE_LABELS
    from pydocs_mcp.harness.ask_your_docs.scope_pin import CODE_SCOPE_WORDS

    assert CODE_SCOPE_WORDS["project"] == CODE_LABELS[ScopeCode.OWN]
    assert scope_note({"project": "x", "code": "project"}) == (
        'Searching only in: project "x", project code only'
    )
    label = tool_step_label("search_codebase", {"query": "routing"}, running=False)
    assert label == f'Searched {CODE_LABELS[ScopeCode.ALL]} for "routing"'
    assert not [seg for seg in _RETIRED if seg in label]


def test_the_picker_title_is_the_on_screen_wording() -> None:
    """AC-45's label: a popover's label is not reachable from AppTest, so this is its pin."""
    pytest.importorskip("streamlit")  # scope_picker imports streamlit at module level
    from pydocs_mcp.harness.ask_your_docs.scope_picker import PICKER_TITLE

    assert PICKER_TITLE == "Where to search"
