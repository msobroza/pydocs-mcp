"""``ask_your_docs.seed_search_with_question``: the harness's own first search.

Off by default. On, ONE ``search_codebase`` runs with the standalone question
verbatim through the agent's own bound tool — same MCP client, same
question-scope interceptor — and the model is shown it as a call that already
completed, so it does not spend a turn repeating the identical query. The
model-turn sidecar stamps that call turn 0 and the model's first message keeps
turn 1.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.agent import ask
from pydocs_mcp.harness.ask_your_docs.first_turn import (
    SEED_SEARCH_TOOL,
    SeedSearchUnavailableError,
    SeededSearch,
    is_seeded_search,
    seeded_search_for,
)
from pydocs_mcp.harness.ask_your_docs.model_turns import proposed_calls
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    ACTIVE_QUESTION_SCOPE,
    intercept_question_scope,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig
from tests.harness.core._runner_contract import conformant_sample

from ._binding_fakes import FakeTurnFinalizer
from ._agent_fakes import (
    FakeActivityToolset,
    FakeAgentFactory,
    FakeRecordingGraph,
    FakeServeSpawn,
)

_QUESTION = "how does routing work?"
_ONE_CELL_PIN = QuestionScope(
    kind=ScopeKind.PIN,
    cells=(ScopeCell("demo", ""),),
    code=ScopeCode.OWN,
    package="fastapi",
)


def _messages(graph: FakeRecordingGraph) -> list:
    return graph.inputs[0]["messages"]


@dataclass(frozen=True)
class _SeedRequest:
    """The adapter's MCPToolCallRequest shape: name, args, override(args=)."""

    name: str
    args: dict[str, Any]

    def override(self, **overrides: Any) -> _SeedRequest:
        return replace(self, **overrides)


async def _intercepted(
    tool: str, args: dict[str, Any], scope: QuestionScope
) -> list[dict[str, Any]]:
    """The arguments the server would receive for ``args`` under ``scope``.

    One list entry per handler call, so a multi-cell pin shows its fan-out. The
    real path binds the same contextvar inside ``ask()``, which is why the seeded
    call needs no scoping of its own.
    """
    sent: list[dict[str, Any]] = []

    async def handler(request: _SeedRequest) -> CallToolResult:
        sent.append(dict(request.args))
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structuredContent={"text": "ok", "items": [], "meta": {}},
        )

    token = ACTIVE_QUESTION_SCOPE.set(scope)
    try:
        await intercept_question_scope(_SeedRequest(tool, dict(args)), handler)
    finally:
        ACTIVE_QUESTION_SCOPE.reset(token)
    return sent


# ── the knob ──


def test_the_seed_is_off_in_the_shipped_config() -> None:
    assert AskYourDocsConfig().seed_search_with_question is False


def test_the_knob_off_builds_no_seeder() -> None:
    assert seeded_search_for(False, FakeActivityToolset().tools) is None


async def test_a_turn_without_a_seeder_sends_only_the_question() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(graph, [], _QUESTION, finalizer=FakeTurnFinalizer())
    assert tools.calls == [], "nothing may run before the model's first turn"
    assert [type(m) for m in _messages(graph)] == [HumanMessage]


# ── one seeded call, the question verbatim ──


async def test_the_seeded_call_asks_the_question_verbatim() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
    assert tools.calls == [(SEED_SEARCH_TOOL, {"query": _QUESTION})]


async def test_the_seeded_call_carries_only_the_query() -> None:
    """The caller never pins: ``scope_interceptor`` owns that for every call."""
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        scope=_ONE_CELL_PIN,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
    proposal = _messages(graph)[1]
    assert proposal.tool_calls[0]["args"] == {"query": _QUESTION}
    assert tools.calls == [(SEED_SEARCH_TOOL, {"query": _QUESTION})]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        pytest.param(
            _ONE_CELL_PIN,
            [{"query": _QUESTION, "package": "fastapi", "scope": "project", "project": "demo"}],
            id="one_cell_pin",
        ),
        pytest.param(
            QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("demo", ""), ScopeCell("api", ""))),
            [{"query": _QUESTION, "project": "demo"}, {"query": _QUESTION, "project": "api"}],
            id="multi_cell_pin_fans_out",
        ),
    ],
)
async def test_the_interceptor_scopes_the_seeded_call_like_a_model_issued_one(
    scope: QuestionScope, expected: list[dict]
) -> None:
    """The seed's args, put through the interceptor under the question's scope.

    A multi-target pin needs no choice of a primary cell: the interceptor fans the
    ONE seeded call out over every cell and merges the labeled results, exactly as
    it would for the model's own first search.
    """
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        scope=scope,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
    seeded_args = _messages(graph)[1].tool_calls[0]["args"]

    sent = await _intercepted(SEED_SEARCH_TOOL, seeded_args, scope)

    assert sent == expected


async def test_the_question_the_model_reads_still_carries_the_scope_note() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("demo", ""),))
    await ask(
        graph,
        [],
        _QUESTION,
        scope=pin,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
    [question] = [m for m in _messages(graph) if isinstance(m, HumanMessage)]
    assert question.content == f"[pinned scope: project=demo] {_QUESTION}"


# ── shown to the model as a completed call ──


async def test_the_model_is_shown_the_call_and_its_result() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
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
    await ask(
        graph,
        [],
        _QUESTION,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
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
        finalizer=FakeTurnFinalizer(),
    )
    assert tools.calls == []


# ── the model-turn sidecar ──


async def test_the_seeded_call_is_stamped_turn_zero() -> None:
    graph, tools = FakeRecordingGraph(), FakeActivityToolset()
    await ask(
        graph,
        [],
        _QUESTION,
        seed_search=seeded_search_for(True, tools.tools),
        finalizer=FakeTurnFinalizer(),
    )
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
        await SeededSearch(tuple(narrowed)).messages_for(_QUESTION)
    assert SEED_SEARCH_TOOL in str(exc.value)
    assert "get_overview" in str(exc.value), "the message names what IS bound"


# ── the eval binding runs the same seed ──

#: A campaign row's rendered prompt: the shared task scaffold around the question.
_SCAFFOLDED = f"Answer citing the file and line.\n\nQuestion: {_QUESTION}"


def _campaign_sample(*, with_question: bool) -> dict[str, object]:
    """A campaign row: the scaffolded prompt, plus the bare question when asked for."""
    row: dict[str, object] = {**conformant_sample(), "rendered_prompt": _SCAFFOLDED}
    return {**row, "question": _QUESTION} if with_question else row


async def _run_campaign_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample: dict[str, object],
    *,
    seed_search: bool,
) -> tuple[list[Any], list[tuple[str, dict[str, Any]]]]:
    """One sample through the eval binding's run seam: ``(payload, tool calls)``."""
    factory, tools = FakeAgentFactory(), FakeActivityToolset()
    monkeypatch.setattr(agent_module, "build_agent_with_scope_capabilities", factory)
    monkeypatch.setattr(binding, "_serve_session_tools", FakeServeSpawn(tools.tools).session)
    settings = binding.AskYourDocsRunnerSettings(
        workspace=str(tmp_path / "ws"),
        model="fake-model",
        trace_root=str(tmp_path / "traces"),
        harness=AskYourDocsConfig(seed_search_with_question=seed_search),
    )
    await binding._build_and_execute(
        sample=sample,
        settings=settings,
        overrides=binding.PromptOverrides(),
        skill_override=None,
        task_name=None,
        trace_env={},
    )
    return factory.graph.payloads[-1], tools.calls


async def test_the_campaign_path_seeds_when_the_arm_turns_the_knob_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The eval binding assembles its own payload, so it seeds on its own."""
    sample = _campaign_sample(with_question=True)

    unseeded, calls = await _run_campaign_sample(tmp_path, monkeypatch, sample, seed_search=False)
    assert [m.content for m in unseeded] == [_SCAFFOLDED], "the model reads the prompt alone"
    assert calls == []

    (human, proposal, result), _calls = await _run_campaign_sample(
        tmp_path, monkeypatch, sample, seed_search=True
    )
    assert isinstance(human, HumanMessage)
    assert is_seeded_search(proposal) and isinstance(result, ToolMessage)


async def test_the_campaign_seed_asks_the_bare_question_not_the_scaffolded_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scaffold is instructions for the model, not a query (#384): the 0.90 at
    k=10 that justifies the seed was measured on the question alone."""
    sample = _campaign_sample(with_question=True)

    (human, _proposal, _result), calls = await _run_campaign_sample(
        tmp_path, monkeypatch, sample, seed_search=True
    )

    assert calls == [(SEED_SEARCH_TOOL, {"query": _QUESTION})]
    assert human.content == _SCAFFOLDED, "the model still reads the scaffolded prompt"


async def test_a_campaign_sample_without_a_question_seeds_its_rendered_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``question`` is an optional row key: a row without one seeds what it has."""
    sample = _campaign_sample(with_question=False)

    _payload, calls = await _run_campaign_sample(tmp_path, monkeypatch, sample, seed_search=True)

    assert calls == [(SEED_SEARCH_TOOL, {"query": _SCAFFOLDED})]


def test_the_panel_says_the_harness_searched_first() -> None:
    """The MCP capture stamps every call ``initiator: "model"`` — the server
    cannot see who composed it — so the panel is where the seed is named."""
    from pydocs_mcp.harness.ask_your_docs.activity_labels import seeded_search_note

    assert seeded_search_note(_QUESTION) == f'Searched your question first: "{_QUESTION}"'
