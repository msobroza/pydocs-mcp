"""Headless ask-agent runner + architecture registry (spec §3.3.3).

The optimize layer drives the product ask agent in-process: one question in,
one ``AskTranscript`` out. ``ask_architecture_registry`` maps every product
``agent_registry`` name to a thin bridge whose ``build`` delegates to the
product ``build_agent(..., architecture=<name>, prompts=…)`` — prompt
candidates flow through bridges via the product's single assembly site, so
new product architectures become searchable with zero new harness code.

Everything that touches the product library sits behind
``_require_ask_extra()``; ``import pydocs_eval.optimize`` never pulls
langgraph (the ``[ask]`` extra is opt-in like ``[retrieval]``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydocs_eval.optimize._agent_track_binding import DEFAULT_TASK_TIMEOUT_SECONDS
from pydocs_eval.registries import (
    _Registry,  # WHY: same in-repo registry mechanic as the optimize axes; a second copy would drift
)

if TYPE_CHECKING:
    from pydocs_mcp.ask_your_docs.agent import AskPrompts

# WHY: prompt campaigns pin every candidate to ONE architecture (the
# no-joint-search rule, spec §3.3.2/§4.2). "text_react" is the product
# agent_registry's plain text ReAct graph — the ask default on text models.
_DEFAULT_ASK_ARCHITECTURE = "text_react"

# The product agent_registry names bridged one-to-one (spec §7-Q1), with the
# descriptions the dry-run listing shows. Adding a product architecture means
# adding ONE row here.
_PRODUCT_BRIDGES: Mapping[str, str] = {
    "auto": "capability-routed product default (text_react or vision_subagent)",
    "inline": "single ReAct graph with images inline in the conversation",
    "text_react": "plain text ReAct agent over the nine task-shaped tools",
    "vision_subagent": "text ReAct plus a one-shot vision extraction subagent",
}


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One tool invocation as the transcript records it: name + args digest."""

    tool_name: str
    args_digest: str


@dataclass(frozen=True, slots=True)
class AskTranscript:
    """What one ask-agent run produced — everything gates and judges read."""

    answer: str
    tool_calls: tuple[ToolCallRecord, ...]
    turns: int
    cost_usd: float
    wall_seconds: float


@dataclass(frozen=True, slots=True)
class AskBuildRequest:
    """Everything a bridge needs to assemble one candidate's agent."""

    workspace: Path
    model: str
    base_url: str | None
    prompts: AskPrompts | None
    pydocs_config: Path | None
    max_agent_turns: int


@runtime_checkable
class AskRunner(Protocol):
    """One question in, one transcript out (mirrors the agent-track AgentRunner)."""

    async def run(self, question: str) -> AskTranscript: ...


def _ask_extra_missing_module() -> str | None:
    """Name the first missing ``[ask]`` dependency, or ``None`` when complete.

    ``find_spec`` only (no import side effects) — the ``ensure_available``
    shape from the skillopt adapter.
    """
    for module in ("langgraph", "pydocs_mcp"):
        if importlib.util.find_spec(module) is None:
            return module
    return None


def _require_ask_extra() -> None:
    """Raise an actionable ``RuntimeError`` when the ``[ask]`` extra is absent."""
    missing = _ask_extra_missing_module()
    if missing is not None:
        raise RuntimeError(
            f"{missing!r} is not installed — run "
            f'pip install "pydocs-mcp-eval[ask]" to drive the ask agent '
            f"(the product version floor is pinned in benchmarks/pyproject.toml)"
        )


def _resolve_build_agent() -> Callable[..., Coroutine[object, object, object]]:
    """Lazily import the product ``build_agent`` (behind the extras guard)."""
    from pydocs_mcp.ask_your_docs.agent import build_agent

    return build_agent


def make_ask_prompts(
    *, system_prompt: str | None = None, rewrite_prompt: str | None = None
) -> AskPrompts:
    """Construct the product ``AskPrompts`` override bundle (lazy import)."""
    _require_ask_extra()
    from pydocs_mcp.ask_your_docs.agent import AskPrompts

    return AskPrompts(system_prompt=system_prompt, rewrite_prompt=rewrite_prompt)


@dataclass(frozen=True, slots=True)
class AskArchitectureSpec:
    """A named way of assembling the ask agent for evaluation (spec §3.2.2).

    ``build`` returns the product's ``(graph, llm)`` pair; the runner drives
    the graph. Bridges delegate to the product registry via ``build_agent``'s
    ``architecture=`` keyword, so the candidate's prompts thread through the
    product's one prompt-assembly site.
    """

    name: str = ""
    description: str = ""

    async def build(self, request: AskBuildRequest) -> object:
        _require_ask_extra()
        build_agent = _resolve_build_agent()
        return await build_agent(
            str(request.workspace),
            request.model,
            base_url=request.base_url,
            pydocs_config=str(request.pydocs_config) if request.pydocs_config else None,
            architecture=self.name,
            prompts=request.prompts,
        )


ask_architecture_registry: _Registry[AskArchitectureSpec] = _Registry()


def _bridge_factory(arch_name: str, description: str) -> Callable[[], AskArchitectureSpec]:
    """One registrable zero-arg factory per product name.

    WHY a factory and not a subclass: ``_Registry.build`` instantiates the
    registered entry with no args, and slotted-dataclass subclasses cannot
    re-declare inherited fields as defaults — a closure-captured factory
    gives each name its spec without fighting ``__slots__``.
    """

    def factory() -> AskArchitectureSpec:
        return AskArchitectureSpec(name=arch_name, description=description)

    factory.__name__ = f"bridge_{arch_name}"
    return factory


for _name, _description in _PRODUCT_BRIDGES.items():
    ask_architecture_registry.register(_name)(_bridge_factory(_name, _description))  # type: ignore[arg-type]


@dataclass(slots=True)
class LangGraphAskRunner:
    """The real runner: builds the selected architecture once, asks per task.

    Construction is guarded (AC-18): instantiating without the ``[ask]``
    extra raises the actionable RuntimeError. The agent graph is built
    lazily on the first ``run`` and reused across questions — one MCP server
    subprocess per candidate, not per question.
    """

    request: AskBuildRequest
    architecture: str = _DEFAULT_ASK_ARCHITECTURE
    task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS
    _graph: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _require_ask_extra()

    async def _agent(self) -> object:
        if self._graph is None:
            spec = ask_architecture_registry.build(self.architecture)
            graph, _llm = await spec.build(self.request)  # type: ignore[misc]
            self._graph = graph
        return self._graph

    async def run(self, question: str) -> AskTranscript:
        """Ask one question and normalize the message stream to a transcript.

        WHY cost_usd is 0.0 here: the agent runs against an OpenAI-compatible
        endpoint whose pricing the harness cannot know (often a local
        server); the metered spend is the judge arm, bounded by
        ``budget.max_judge_calls`` — the documented spend asymmetry, mirrored
        in the runbook.

        A runaway candidate (recursion-limit hit) or a hung tool call
        (task timeout) yields a sentinel FAILED transcript rather than an
        exception: one bad candidate must cost its own sample, never the
        whole campaign — the gates fail the sample deterministically.
        """
        import asyncio

        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.errors import GraphRecursionError

        graph = await self._agent()
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                graph.ainvoke(  # type: ignore[attr-defined]
                    {"messages": [HumanMessage(question)]},
                    {"recursion_limit": 2 * self.request.max_agent_turns},
                ),
                timeout=self.task_timeout_seconds,
            )
        except (GraphRecursionError, TimeoutError):
            # turns = cap + 1 fails the max_turns gate; the empty answer
            # fails min_answer_chars — the sample scores 0, the run goes on.
            return AskTranscript(
                answer="",
                tool_calls=(),
                turns=self.request.max_agent_turns + 1,
                cost_usd=0.0,
                wall_seconds=time.monotonic() - started,
            )
        wall = time.monotonic() - started
        messages = result["messages"]
        ai_messages = [m for m in messages if isinstance(m, AIMessage)]
        calls = tuple(
            ToolCallRecord(tool_name=tc["name"], args_digest=_digest(tc.get("args", {})))
            for m in ai_messages
            for tc in getattr(m, "tool_calls", ())
        )
        answer = _flatten_answer(messages[-1].content) if messages else ""
        return AskTranscript(
            answer=answer,
            tool_calls=calls,
            turns=len(ai_messages),
            cost_usd=0.0,
            wall_seconds=wall,
        )


def _digest(args: object) -> str:
    """Short stable digest of a tool call's args for the transcript record."""
    rendered = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:12]


def _flatten_answer(content: object) -> str:
    """A final message's text — content-block lists flatten to their text parts.

    Multimodal replies carry a list of typed blocks; str() on that list would
    put a Python repr in front of the gates and the judge.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(part for part in parts if part)
    return str(content)


_EMPTY_TRANSCRIPT = AskTranscript(answer="", tool_calls=(), turns=0, cost_usd=0.0, wall_seconds=0.0)


@dataclass(slots=True)
class FakeAskRunner:
    """Scripted offline double: canned transcripts keyed by question.

    An unscripted question returns the empty transcript (which fails the
    shipped gates deterministically). ``calls`` lets tests prove per-sample
    resume never re-runs the agent (spec AC-11, AC-19).
    """

    scripted: Mapping[str, AskTranscript]
    calls: int = field(default=0, init=False)

    async def run(self, question: str) -> AskTranscript:
        self.calls += 1
        return self.scripted.get(question, _EMPTY_TRANSCRIPT)
