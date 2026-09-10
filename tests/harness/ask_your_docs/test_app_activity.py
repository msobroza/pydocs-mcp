"""The activity panel on the real page (AppTest; PROPOSAL §6, TDD 7 + 8).

``build_agent`` is swapped for a scripted ReAct graph over the named fakes, while the real
``ask`` streams it — so these runs cross the same stream → events → builder → view path a
real question does, minus the network and the serve child.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("langgraph")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.page_turn import TECHNICAL_TOGGLE_KEY

from ._agent_fakes import ACTIVITY_SCRIPT, FakeActivityGraphBuilder
from ._connection_fakes import FakeBearer

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import PAGE_LOGGER, page, page_env, status_line, write_config

_TOKEN = "tok-sentinel-abcd"
_QUESTION = "how does routing work?"
_ANSWER = "Routing is handled by APIRouter."
_DONE = re.compile(r"Done in \d+\.\d s · 5 steps · 1 failed · 2 files · reasoning shown")
_LEAKY_SCRIPT = [
    {
        "reasoning": f"I will search with the key {_TOKEN} as the query. ",
        "text": "",
        "tool_calls": [{"id": "c1", "name": "search_codebase", "args": {"query": _TOKEN}}],
    },
    {"reasoning": "", "text": "done", "tool_calls": []},
]


async def _same_question(_llm, _history, question, **_kwargs):
    return question


def _asked(tmp_path, monkeypatch, *, script=None, ui: str = "", reformulate=_same_question):
    config = write_config(tmp_path, model="main-a")
    if ui:
        with Path(config).open("a", encoding="utf-8") as handle:
            handle.write("  ui:\n" + ui)
    monkeypatch.setenv("PYDOCS_CONFIG", config)
    builder = FakeActivityGraphBuilder(script)
    monkeypatch.setattr(agent_module, "build_agent", builder)
    monkeypatch.setattr(reformulation_module, "reformulate", reformulate)
    at = page(connection_bearer=FakeBearer(_TOKEN))
    at.run()
    at.chat_input[0].set_value(_QUESTION).run()
    assert not at.exception, at.exception
    return at, builder


def _every_text(at) -> list[str]:
    elements = [*at.markdown, *at.code, *at.caption, *at.text, *at.error, *at.info]
    return [str(e.value) for e in elements] + [b.label for b in [*at.status, *at.expander]]


def test_a_turn_ends_complete_and_collapsed_with_its_summary(tmp_path, monkeypatch) -> None:
    at, _ = _asked(tmp_path, monkeypatch)
    [status] = at.status
    assert status.state == "complete" and _DONE.fullmatch(status.label), status.label
    assert status.proto.expanded is False
    assert _ANSWER in [m.value for m in at.markdown]
    assert at.session_state.messages == [("user", _QUESTION), ("assistant", _ANSWER)]
    assert [e.label for e in at.expander if e.label.startswith("Also looked")] == [
        "Also looked at (2)"
    ]


def test_a_rerun_replays_the_panel_without_running_the_agent(tmp_path, monkeypatch) -> None:
    at, builder = _asked(tmp_path, monkeypatch)
    label = at.status[0].label
    at.run()
    assert not at.exception, at.exception
    [status] = at.status
    assert status.label == label and status.state == "complete"
    assert status.proto.expanded is False and builder.builds == 1
    assert _ANSWER in [m.value for m in at.markdown]


def _assert_failed_turn(at) -> None:
    assert not at.exception, at.exception
    [status] = at.status
    assert status.state == "error" and status.proto.expanded is True
    assert status.label.startswith("Stopped after 4 steps · ")
    assert status.label.endswith(" · an error stopped the turn")
    assert [e.value for e in at.error] == ["RuntimeError: upstream rejected Bearer …abcd"]
    assert f'Your question was not answered: "{_QUESTION}"' in [c.value for c in at.caption]
    assert at.session_state.messages == [("user", _QUESTION), ("assistant", "")]


def test_a_failed_turn_keeps_its_steps_and_survives_a_rerun(tmp_path, monkeypatch) -> None:
    script = [ACTIVITY_SCRIPT[0], {"error": f"upstream rejected Bearer {_TOKEN}"}]
    at, _ = _asked(tmp_path, monkeypatch, script=script)
    _assert_failed_turn(at)
    _assert_failed_turn(at.run())


def test_the_panel_off_keeps_todays_page(tmp_path, monkeypatch) -> None:
    at, _ = _asked(tmp_path, monkeypatch, ui="    activity:\n      enabled: false\n")
    assert not at.status and not at.expander
    assert not [c for c in at.caption if c.value.startswith("Reasoning:")]
    assert not [t for t in at.toggle if t.label == "Show technical details"]
    assert _ANSWER in [m.value for m in at.markdown]
    assert "activity" not in at.session_state or not at.session_state["activity"]


def test_no_credential_reaches_any_element(tmp_path, monkeypatch) -> None:
    ui = "    activity:\n      technical_details: true\n"
    at, _ = _asked(tmp_path, monkeypatch, script=_LEAKY_SCRIPT, ui=ui)
    texts = _every_text(at)
    assert not [text for text in texts if _TOKEN in text]
    assert any("…abcd" in text for text in texts)  # the step is shown, masked
    assert any(e.label == "Details" for e in at.expander)


def test_the_reasoning_caption_is_its_own_line(tmp_path, monkeypatch) -> None:
    at, _ = _asked(tmp_path, monkeypatch)
    at.run()
    [caption] = [c.value for c in at.caption if c.value.startswith("Reasoning:")]
    assert caption == "Reasoning: shown (seen in answers)"
    assert " · " not in caption and "vision:" not in caption
    assert status_line(at).startswith("llm.internal · main-a · ")


def test_the_technical_toggle_opens_step_details(tmp_path, monkeypatch) -> None:
    at, _ = _asked(tmp_path, monkeypatch)
    assert not [e for e in at.expander if e.label == "Details"]
    at.toggle(key=TECHNICAL_TOGGLE_KEY).set_value(True).run()
    assert not at.exception, at.exception
    assert len([e for e in at.expander if e.label == "Details"]) == 4


def test_a_rephrased_question_is_noted(tmp_path, monkeypatch) -> None:
    async def rephrase(_llm, _history, _question, **_kwargs):
        return "where is routing handled in fastapi"

    at, _ = _asked(tmp_path, monkeypatch, reformulate=rephrase)
    notes = [t.value for t in at.text]
    assert '✓ Rephrased your question as "where is routing handled in fastapi"' in notes


def test_one_counts_only_turn_activity_record_per_turn(tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=PAGE_LOGGER):
        _asked(tmp_path, monkeypatch, script=_LEAKY_SCRIPT)
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == PAGE_LOGGER]
    activity = [r for r in records if r.get("event") == "turn_activity"]
    assert len(activity) == 1
    assert set(activity[0]) == {
        "event", "state", "steps", "tools", "failed", "files", "reasoning", "elapsed_s"
    }  # fmt: skip
    assert _TOKEN not in caplog.text and "search with the key" not in caplog.text
