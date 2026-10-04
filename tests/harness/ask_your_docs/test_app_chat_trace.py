"""The chat page under ``ask_your_docs.trace`` (AppTest over the real page).

``build_agent`` is swapped for a scripted ReAct graph and ``reformulate`` for a fixed rewrite,
while the real ``ask`` runs the turn. The ``serve_tools_opener`` seam stands in for the page's
serve child: a named fake that, opened through the real ``traced_serve_opener``, records like
a traced ``serve`` — so what lands on disk is what the page itself decided to keep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("langgraph")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.db import default_cache_dir
from pydocs_mcp.harness.ask_your_docs.page_trace import traced_serve_opener

from ._agent_fakes import FakeActivityGraphBuilder, FakeRewrite
from ._connection_fakes import FakeBearer

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import page, page_env, write_config
from ._serve_session_fakes import FakeServeToolsOpener

_TYPED = "how does routing work?"
_STANDALONE = "where is routing handled in fastapi?"
_ANSWER = "Routing is handled by APIRouter."
_TRACE_ON = "  trace:\n    enabled: true\n"


class _AskSpy:
    """Stands in for ``ask``: records every keyword it was called with."""

    def __init__(self) -> None:
        self.keywords: list[set[str]] = []

    async def __call__(self, _agent, _history, _question, **kwargs: Any) -> str:
        self.keywords.append(set(kwargs))
        return "an answer"


def _page(tmp_path: Path, monkeypatch, *, trace_block: str = "", opener: Any = None):
    config = write_config(tmp_path, model="main-a")
    with Path(config).open("a", encoding="utf-8") as handle:
        handle.write(trace_block)
    monkeypatch.setenv("PYDOCS_CONFIG", config)
    monkeypatch.setattr(
        agent_module, "build_agent_with_scope_capabilities", FakeActivityGraphBuilder()
    )
    monkeypatch.setattr(reformulation_module, "reformulate", FakeRewrite(_STANDALONE))
    at = page(connection_bearer=FakeBearer(), serve_tools_opener=opener or FakeServeToolsOpener())
    at.run()
    assert not at.exception, at.exception
    return at


def _ask(at, question: str = _TYPED):
    at.chat_input[0].set_value(question).run()
    assert not at.exception, at.exception
    return at


def _question_record(trace_dir: Path, number: int) -> dict[str, Any]:
    path = trace_dir / "questions" / str(number) / "question.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _trajectory_dirs(root: Path) -> list[Path]:
    return [path for path in root.iterdir() if path.name != "blobs"]


def test_every_answered_question_is_kept_under_the_live_child(tmp_path, monkeypatch) -> None:
    root = tmp_path / "traces"
    child = FakeServeToolsOpener()
    at = _page(
        tmp_path,
        monkeypatch,
        trace_block=_TRACE_ON,
        opener=traced_serve_opener(child.with_env, root),
    )
    _ask(_ask(at), "and who calls it?")
    [trace_dir] = _trajectory_dirs(root)
    assert _question_record(trace_dir, 1) == {
        "schema_version": 1,
        "question": _TYPED,
        "standalone_question": _STANDALONE,
        "answer": _ANSWER,
        "finalized": False,
    }
    assert _question_record(trace_dir, 2)["question"] == "and who calls it?"


def test_a_restarted_child_keeps_the_next_question_under_its_new_id(tmp_path, monkeypatch) -> None:
    root = tmp_path / "traces"
    child = FakeServeToolsOpener()
    at = _page(
        tmp_path,
        monkeypatch,
        trace_block=_TRACE_ON,
        opener=traced_serve_opener(child.with_env, root),
    )
    _ask(at)
    child.sessions[-1].die()
    _ask(at, "and who calls it?")
    assert [i.value for i in at.info if "docs server had stopped" in i.value]
    records = sorted(
        (_question_record(trace_dir, 1)["question"] for trace_dir in _trajectory_dirs(root)),
    )
    assert records == ["and who calls it?", _TYPED]  # one question under each child's id


def test_the_knob_on_over_an_untraced_seam_still_answers(tmp_path, monkeypatch) -> None:
    at = _ask(_page(tmp_path, monkeypatch, trace_block=_TRACE_ON))
    assert _ANSWER in [m.value for m in at.markdown]
    assert not (default_cache_dir() / "chat-traces").exists()


def test_the_knob_off_calls_ask_exactly_as_before(tmp_path, monkeypatch) -> None:
    spy = _AskSpy()
    monkeypatch.setattr(agent_module, "ask", spy)
    _ask(_page(tmp_path, monkeypatch))
    [keywords] = spy.keywords
    assert "trace_sink" not in keywords
    assert "on_final" in keywords and "max_agent_turns" in keywords  # the page's own keywords


def test_the_knob_adds_no_page_state(tmp_path, monkeypatch) -> None:
    child = FakeServeToolsOpener()
    traced = traced_serve_opener(child.with_env, tmp_path / "traces")
    kept_on = _ask(_page(tmp_path, monkeypatch, trace_block=_TRACE_ON, opener=traced))
    on_keys = set(kept_on.session_state.filtered_state)
    kept_off = _ask(_page(tmp_path, monkeypatch))
    assert on_keys == set(kept_off.session_state.filtered_state)
