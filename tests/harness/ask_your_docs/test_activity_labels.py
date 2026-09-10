"""Plain-language step labels, outcomes, notes and citations (activity panel, PROPOSAL §3, TDD 4).

Pure functions over tool names, arguments and the frozen MCP envelope (``{text, items,
meta}``, docs/tool-contracts.md §2–§3) — no langchain, no Streamlit.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.harness.ask_your_docs.activity_labels import (
    failure_reason,
    rephrase_note,
    scope_note,
    tool_step_label,
    vision_step_label,
)
from pydocs_mcp.harness.ask_your_docs.activity_outcomes import (
    Citation,
    citations_from_items,
    failure_outcome,
    meta_line,
    meta_notes,
    split_cited,
    summarize_tool_result,
)
from pydocs_mcp.harness.ask_your_docs.prompts import BUDGET_MESSAGE

_T = "fastapi.routing.APIRouter"


@pytest.mark.parametrize(
    ("name", "args", "done", "running"),
    [
        ("search_codebase", {"query": "routing"}, 'Searched all code for "routing"', None),
        ("search_codebase", {"query": "q", "scope": "project"}, 'Searched project code for "q"',
         'Searching project code for "q" …'),
        ("search_codebase", {"query": "q", "scope": "deps", "package": "vllm"},
         'Searched dependencies for "q" in vllm', None),
        ("search_codebase", {"query": "q", "kind": "api"},
         'Searched all code for symbols matching "q"', None),
        ("search_codebase", {"query": "q", "kind": "decision"},
         'Searched all code for decisions about "q"', None),
        ("get_symbol", {"target": _T}, f"Looked up {_T}", f"Looking up {_T} …"),
        ("get_symbol", {"target": _T, "depth": "tree"}, f"Outlined {_T}", f"Outlining {_T} …"),
        ("get_symbol", {"target": _T, "depth": "source"}, f"Read the source of {_T}",
         f"Reading the source of {_T} …"),
        ("get_context", {"targets": ["a", "b", "c", "d"]}, "Gathered context for a, b (+2 more)",
         "Gathering context for a, b (+2 more) …"),
        ("get_context", {"targets": ["a"]}, "Gathered context for a", None),
        ("get_references", {"target": _T}, f"Found callers of {_T}", f"Finding callers of {_T} …"),
        ("get_references", {"target": _T, "direction": "callees"}, f"Found what {_T} calls", None),
        ("get_references", {"target": _T, "direction": "inherits"},
         f"Checked the class hierarchy of {_T}", f"Checking the class hierarchy of {_T} …"),
        ("get_references", {"target": _T, "direction": "impact"},
         f"Estimated what changing {_T} affects", None),
        ("get_references", {"target": _T, "direction": "governed_by"},
         f"Found decisions governing {_T}", None),
        ("get_why", {"query": "why cache"}, 'Looked for design decisions about "why cache"',
         'Looking for design decisions about "why cache" …'),
        ("get_why", {"targets": ["a.py", "b.c"]}, "Looked for design decisions about a.py, b.c",
         None),
        ("get_overview", {"package": "fastapi"}, "Got an overview of fastapi",
         "Getting an overview of fastapi …"),
        ("get_overview", {}, "Got an overview of the workspace", None),
        ("grep", {"pattern": "x(", "glob": "*.py"}, "Searched file text for /x(/ in *.py",
         "Searching file text for /x(/ in *.py …"),
        ("grep", {"pattern": "x", "path": "src"}, "Searched file text for /x/ in src", None),
        ("grep", {"pattern": "x"}, "Searched file text for /x/ in the project", None),
        ("glob", {"pattern": "**/*_test.py"}, "Listed files matching **/*_test.py",
         "Listing files matching **/*_test.py …"),
        ("read_file", {"file_path": "a.py", "offset": 10, "limit": 31}, "Opened a.py:10–40",
         "Opening a.py:10–40 …"),
        ("read_file", {"file_path": "a.py", "offset": 10}, "Opened a.py from line 10", None),
        ("read_file", {"file_path": "a.py"}, "Opened a.py", None),
        ("reinspect_images", {"names": ["a.png"], "question": "q"}, "Looked at image a.png again",
         "Looking at image a.png again …"),
        ("reinspect_images", {"names": ["a.png", "b.png"]}, "Looked at images a.png, b.png again",
         None),
        ("frobnicate", {"x": 1}, "Called frobnicate", "Calling frobnicate …"),
    ],
)  # fmt: skip
def test_every_tool_has_a_done_and_a_running_label(name, args, done, running) -> None:
    assert tool_step_label(name, args, running=False) == done
    if running is not None:
        assert tool_step_label(name, args, running=True) == running


def test_argument_values_are_clipped_to_sixty_characters() -> None:
    label = tool_step_label("search_codebase", {"query": "x" * 200}, running=False)
    assert label == f'Searched all code for "{"x" * 59}…"'


def test_the_vision_node_label() -> None:
    assert vision_step_label(running=False) == "Analyzed the attached images"
    assert vision_step_label(running=True) == "Analyzing the attached images …"


# ── outcomes ──


def _envelope(items: list[dict], **meta) -> dict:
    return {"text": "t", "items": items, "meta": {"project": "demo", "branch": "main", **meta}}


def _rows(*spans: tuple[str, int]) -> list[dict]:
    return [{"path": path, "start_line": line, "end_line": line + 9} for path, line in spans]


@pytest.mark.parametrize(
    ("name", "args", "items", "outcome"),
    [
        ("search_codebase", {}, _rows(("a.py", 1), ("b.py", 2)), "2 matches in 2 files"),
        ("search_codebase", {}, _rows(("a.py", 1)), "1 match in 1 file"),
        ("search_codebase", {}, [], "no matches"),
        ("get_symbol", {}, _rows(("a.py", 42)), "a.py:42–51"),
        ("get_symbol", {}, [], "no match"),
        ("get_context", {}, _rows(("a.py", 1), ("a.py", 20), ("b.py", 3)), "3 symbols"),
        ("get_references", {}, _rows(("a.py", 1), ("b.py", 1)), "2 callers"),
        ("get_references", {"direction": "callees"}, _rows(("a.py", 1)), "1 callee"),
        ("get_references", {"direction": "impact"}, _rows(("a.py", 1)), "1 edge"),
        ("get_references", {"direction": "governed_by"}, [], "no decisions"),
        ("get_why", {}, [{"decision_id": 1}, {"decision_id": 2}], "2 decisions"),
        ("get_overview", {}, [], "demo · main"),
        ("grep", {}, _rows(("a.py", 1), ("a.py", 5), ("b.py", 9)), "3 matching lines in 2 files"),
        ("glob", {}, [{"path": "a.py"}, {"path": "b.py"}], "2 files"),
        ("read_file", {}, [{"path": "a.py", "start_line": 10, "end_line": 40}], "31 lines"),
    ],
)
def test_outcomes_come_from_the_items_and_meta(name, args, items, outcome) -> None:
    assert summarize_tool_result(name, args, _envelope(items), "raw text") == outcome


def test_outcomes_without_an_envelope_use_the_first_line_of_text() -> None:
    assert summarize_tool_result("frobnicate", {}, None, "first\nsecond") == "first"
    assert summarize_tool_result("search_codebase", {}, None, "") == ""
    assert summarize_tool_result("reinspect_images", {}, None, BUDGET_MESSAGE) == "budget used up"
    assert summarize_tool_result("reinspect_images", {}, None, "A red button.") == "done"


def test_overview_without_a_branch_names_only_the_project() -> None:
    envelope = {"text": "t", "items": [], "meta": {"project": "demo", "branch": None}}
    assert summarize_tool_result("get_overview", {}, envelope, "") == "demo"


def test_a_failure_shows_its_first_line() -> None:
    assert failure_outcome("invalid regex: got 'x('\ntrace…") == "failed: invalid regex: got 'x('"
    assert failure_outcome("") == "failed"


# ── notes, in words (never colour alone) ──


def test_meta_notes() -> None:
    meta = {"truncated": True, "index_stale": True, "suggestion": "[suggestion: try grep]"}
    assert meta_notes(meta) == (
        "Results were cut off at the limit",
        "The index is older than your checkout",
        "Tool hint: try grep",
    )
    assert meta_notes({"truncated": False, "index_stale": False, "suggestion": None}) == ()
    assert meta_notes({"resolution": "syntactic"}) == (
        "Reference graph matches by name (syntactic), so some calls may be missed",
    )
    assert meta_notes({"resolution": "unavailable"}) == (
        "Reference graph not available for this language",
    )
    assert meta_notes(None) == ()


# ── citations ──


def test_citations_keep_rows_with_a_path_deduplicated() -> None:
    items = [
        {"path": "a.py", "start_line": 3, "end_line": 9, "qualified_name": "m.f", "package": "p"},
        {"path": "a.py", "start_line": 3, "end_line": 9, "qualified_name": "m.f"},
        {"path": None, "qualified_name": "m.g"},
        {"decision_id": 1},
        {"path": "b.py", "from_qualified_name": "m.caller"},
    ]
    assert citations_from_items(items) == (
        Citation("a.py", 3, 9, "m.f", "p"),
        Citation("b.py", None, None, "m.caller", None),
    )
    assert [c.label for c in citations_from_items(items)] == ["a.py:3", "b.py"]
    assert citations_from_items(None) == ()


def test_cited_means_the_answer_names_the_path_or_the_qualified_name() -> None:
    by_path = Citation("src/run.py", 112, None, None, None)
    by_name = Citation("src/base.py", 39, None, "BaseIndexStore.append", None)
    unused = Citation("tests/t.py", 20, None, "t.helper", None)
    answer = "BaseIndexStore.append is called from src/run.py:112."
    assert split_cited((by_path, by_name, unused), answer) == ((by_path, by_name), (unused,))


# ── notes outside tool calls ──


def test_scope_note_only_when_a_pin_applies() -> None:
    assert scope_note({}) is None
    assert scope_note({"code": "all"}) is None
    assert scope_note({"project": "example_needle"}) == (
        'Scope: project "example_needle" (pinned by you)'
    )
    assert scope_note({"project": "x", "package": "y", "code": "deps"}) == (
        'Scope: project "x", package "y", dependencies only (pinned by you)'
    )


def test_rephrase_note_only_when_the_question_really_changed() -> None:
    assert rephrase_note("Who calls X?", "who calls  x") is None
    assert rephrase_note("who calls it?", "callers of X") == (
        'Rephrased your question as "callers of X"'
    )


@pytest.mark.parametrize(
    ("exc_name", "reason"),
    [
        ("BearerRejectedError", "the model endpoint rejected the request"),
        ("AuthenticationError", "the model endpoint rejected the request"),
        ("GraphRecursionError", "the agent hit its step limit"),
        ("APITimeoutError", "the model endpoint did not answer in time"),
        ("RuntimeError", "an error stopped the turn"),
    ],
)
def test_failure_reasons_are_words(exc_name: str, reason: str) -> None:
    assert failure_reason(exc_name) == reason


def test_meta_line_summarises_the_envelope_for_technical_details() -> None:
    meta = {"project": "demo", "branch": "main", "index_stale": False, "truncated": False}
    assert meta_line(meta) == "project demo · branch main · index up to date · truncated no"
    stale = {"project": "demo", "index_stale": True, "truncated": True, "resolution": "syntactic"}
    assert meta_line(stale) == (
        "project demo · index older than checkout · truncated yes · resolution syntactic"
    )
    assert meta_line({}) == ""
