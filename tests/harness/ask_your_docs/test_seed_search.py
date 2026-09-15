"""``ask_your_docs.seed_search_with_question``: the harness's own first search.

Off by default. On, ONE ``search_codebase`` runs with the standalone question
verbatim through the agent's own bound tool — same MCP client, same pinned
scope — and the model is shown it as a call that already completed, so it does
not spend a turn repeating the identical query. The model-turn sidecar stamps
that call turn 0 and the model's first message keeps turn 1.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pydocs_mcp.harness.ask_your_docs.agent import ask
from pydocs_mcp.harness.ask_your_docs.first_turn import (
    SEED_SEARCH_TOOL,
    SeedSearchUnavailableError,
    SeededSearch,
    is_seeded_search,
    seeded_search_for,
)
from pydocs_mcp.harness.ask_your_docs.model_turns import proposed_calls
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeActivityToolset, FakeRecordingGraph

_QUESTION = "how does routing work?"


def _messages(graph: FakeRecordingGraph) -> list:
    return graph.inputs[0]["messages"]


# ── the knob ──


def test_the_seed_is_off_in_the_shipped_config() -> None:
    assert AskYourDocsConfig().seed_search_with_question is False


def test_the_knob_off_builds_no_seeder() -> None:
    assert seeded_search_for(False, FakeActivityToolset().tools) is None


async def test_a_turn_without_a_seeder_sends_only_the_question() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION)
    assert tools.calls == [], "nothing may run before the model's first turn"
    assert [type(m) for m in _messages(graph)] == [HumanMessage]


# ── one seeded call, the question verbatim ──


async def test_the_seeded_call_asks_the_question_verbatim() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION, seed_search=seeded_search_for(True, tools.tools))
    assert tools.calls == [(SEED_SEARCH_TOOL, {"query": _QUESTION})]


async def test_the_seeded_call_respects_the_pinned_scope() -> None:
    """Exactly what the interceptor would force onto a model-issued call."""
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    scope = {"project": "demo", "package": "fastapi", "code": "project"}
    await ask(graph, [], _QUESTION, scope=scope, seed_search=seeded_search_for(True, tools.tools))
    # The args the call CARRIED, read off the message the model is shown: the
    # fake tool's own signature takes only ``query``, so its call log cannot
    # show the selectors. These are exactly what ``pinned_args`` forces onto a
    # model-issued call.
    proposal = _messages(graph)[1]
    assert proposal.tool_calls[0]["args"] == {
        "query": _QUESTION,
        "project": "demo",
        "package": "fastapi",
        "scope": "project",
    }
    assert tools.calls == [(SEED_SEARCH_TOOL, {"query": _QUESTION})]


async def test_the_question_the_model_reads_still_carries_the_scope_note() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        scope={"project": "demo"},
        seed_search=seeded_search_for(True, tools.tools),
    )
    [question] = [m for m in _messages(graph) if isinstance(m, HumanMessage)]
    assert question.content == f"[pinned scope: project=demo] {_QUESTION}"


# ── shown to the model as a completed call ──


async def test_the_model_is_shown_the_call_and_its_result() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION, seed_search=seeded_search_for(True, tools.tools))
    human, proposal, result = _messages(graph)
    assert isinstance(human, HumanMessage)
    assert isinstance(proposal, AIMessage) and len(proposal.tool_calls) == 1
    assert proposal.tool_calls[0]["name"] == SEED_SEARCH_TOOL
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == proposal.tool_calls[0]["id"]
    rendered = result.content if isinstance(result.content, str) else str(result.content)
    assert _QUESTION in rendered, "the model reads the hits, not just the call"


async def test_the_seeded_message_is_marked_as_the_harness_not_the_model() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION, seed_search=seeded_search_for(True, tools.tools))
    _human, proposal, _result = _messages(graph)
    assert is_seeded_search(proposal) is True
    assert is_seeded_search(AIMessage(content="from the model")) is False


async def test_an_image_turn_is_not_seeded() -> None:
    """The vision architecture reads the LAST message expecting the picture."""

    class _Attachment:
        name = "shot.png"

        def as_content_block(self) -> dict[str, str]:
            return {"type": "image_url", "image_url": "data:image/png;base64,x"}

    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        images=(_Attachment(),),
        seed_search=seeded_search_for(True, tools.tools),
    )
    assert tools.calls == []


# ── the model-turn sidecar ──


async def test_the_seeded_call_is_stamped_turn_zero() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION, seed_search=seeded_search_for(True, tools.tools))
    seeded = _messages(graph)[1]
    model_first_turn = AIMessage(
        content="", tool_calls=[{"name": "get_symbol", "args": {"target": "a.B"}, "id": "c1"}]
    )

    calls = proposed_calls([seeded, model_first_turn])

    assert [(c.turn, c.tool_name) for c in calls] == [
        (0, SEED_SEARCH_TOOL),
        (1, "get_symbol"),
    ]


# ── a deployment that cannot seed ──


async def test_seeding_without_the_search_tool_bound_names_what_is_missing() -> None:
    narrowed = [t for t in FakeActivityToolset().tools if t.name != SEED_SEARCH_TOOL]
    with pytest.raises(SeedSearchUnavailableError) as exc:
        await SeededSearch(tuple(narrowed)).messages_for(_QUESTION, {})
    assert SEED_SEARCH_TOOL in str(exc.value)
    assert "get_overview" in str(exc.value), "the message names what IS bound"


# ── the eval binding runs the same seed ──


async def test_the_campaign_path_seeds_when_the_arm_turns_the_knob_on(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The eval binding assembles its own payload, so it seeds on its own."""
    import contextlib

    import pydocs_mcp.harness.ask_your_docs.agent as agent_module
    import pydocs_mcp.harness.ask_your_docs.binding as binding
    from tests.harness.core._runner_contract import conformant_sample

    tools = FakeActivityToolset()
    seen: list[list] = []

    class _Graph:
        async def ainvoke(self, state, _config):
            seen.append(list(state["messages"]))
            return {"messages": [AIMessage("answer")]}

    async def _fake_build_agent(*_args, **_kwargs):
        return _Graph(), object()

    @contextlib.asynccontextmanager
    async def _fake_session_tools(_settings, _trace_env):
        yield tools.tools

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _fake_session_tools)

    async def _run(seed_on: bool) -> list:
        settings = binding.AskYourDocsRunnerSettings.model_validate(
            {
                "workspace": str(tmp_path / "ws"),
                "model": "fake-model",
                "trace_root": str(tmp_path / "traces"),
                "harness": AskYourDocsConfig(seed_search_with_question=seed_on),
            }
        )
        await binding._build_and_execute(
            sample=conformant_sample(),
            settings=settings,
            overrides=binding.PromptOverrides(),
            skill_override=None,
            task_name=None,
            trace_env={},
        )
        return seen[-1]

    assert [type(m) for m in await _run(False)] == [HumanMessage]
    assert tools.calls == []

    human, proposal, result = await _run(True)
    assert isinstance(human, HumanMessage)
    assert is_seeded_search(proposal) and isinstance(result, ToolMessage)
    assert tools.calls == [(SEED_SEARCH_TOOL, {"query": human.content})]


def test_the_panel_says_the_harness_searched_first() -> None:
    """The MCP capture stamps every call ``initiator: "model"`` — the server
    cannot see who composed it — so the panel is where the seed is named."""
    from pydocs_mcp.harness.ask_your_docs.activity_labels import seeded_search_note

    assert seeded_search_note(_QUESTION) == f'Searched your question first: "{_QUESTION}"'
