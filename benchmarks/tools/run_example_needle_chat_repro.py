"""Ask the example-needle-chat questions through the chat page's agent; write an arm directory.

Vendored from the turn-efficiency repro (ten open-ended questions over
``msobroza/example_needle``). The questions come from the dataset, never from this file.
ONE traced serve child — the chat page's own opener — answers every question in turn,
each through the chat page's own ``ask(..., finalizer=...)`` on its live (streamed) path,
so a question that runs out of steps ends on the Finalized answer exactly as it does on
the page (#375), and the ``finalized`` key of its ``question.json`` says so.

The output is an ARM: ``arm.json`` (rows from the campaign's own ``record_of``, read
through ``read_arm_summary``), ``arm_settings.json``, and one trajectory directory per
question under ``trajectories/<id>/questions/<n>/`` — ``server_events.jsonl``,
``model_turns.json``, ``model_usage.json`` and a ``question.json`` carrying the runner's
per-question record — so ``measure_arm``, ``--report-only`` and the compare verb read it
as they read a campaign arm. Put the two roles side by side: ``<run>/baseline`` and
``<run>/candidate``.

Usage:
    PYTHONPATH=benchmarks/src python benchmarks/tools/run_example_needle_chat_repro.py \\
        --workspace ~/pydocs-index \\
        --config benchmarks/configs/ask_openrouter_example_needle_chat.yaml \\
        --llm-block benchmarks/configs/ask_openrouter_qwen3_8_27b_llm.yaml \\
        --role baseline --out runs/chat/baseline [--split dev] [--max-agent-turns N]

Spends: one chat conversation per question at the block's endpoint (OpenRouter by
default). Nothing in the test suite reaches a model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlanError, resolve_commit
from pydocs_eval.campaign.before_after_arm import (
    ArmSettings,
    ArmSummary,
    record_of,
    write_arm_settings,
    write_arm_summary,
)
from pydocs_eval.campaign.before_after_llm_block import load_arm_llm_block
from pydocs_eval.campaign.budget import HaltReason
from pydocs_eval.campaign.chat_repro import (
    ChatQuestionRun,
    merge_question_record,
    question_record,
)
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import DEFAULT_CHAT_SPLIT, ChatDatasetError
from pydocs_eval.registries import dataset_registry
from pydocs_eval.trajectory.server_capture import trace_recorded
from pydocs_mcp.harness.ask_your_docs.agent import ask, build_agent_with_scope_capabilities
from pydocs_mcp.harness.ask_your_docs.chat_trace import TraceLocation
from pydocs_mcp.harness.ask_your_docs.chat_trace_protocols import ChatTraceSink
from pydocs_mcp.harness.ask_your_docs.finalize import TurnFinalizer
from pydocs_mcp.harness.ask_your_docs.first_turn import came_from_finalize, seeded_search_for
from pydocs_mcp.harness.ask_your_docs.serve_session import page_serve_opener
from pydocs_mcp.retrieval.config.app_config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    AskYourDocsConfig,
    LlmConnectionConfig,
)

_DATASET = "example-needle-chat"
_TRAJECTORIES_DIR = "trajectories"
_ERROR_CHARS = 500


@dataclass(frozen=True, slots=True)
class RunnerOptions:
    """One arm's inputs: what to ask, which agent answers, and where the arm lands."""

    workspace: str
    config: Path
    llm_block: Path
    role: ArmRole
    out: Path
    split: str = DEFAULT_CHAT_SPLIT
    max_agent_turns: int | None = None
    model: str | None = None
    # The product checkout's HEAD; resolved from the checkout when left empty.
    commit: str = ""


@dataclass(frozen=True, slots=True)
class _ArmSession:
    """What every question of one arm shares: the graph, its tools, the trace, the settings."""

    graph: Any
    finalizer: TurnFinalizer
    tools: Sequence[Any]
    trace: TraceLocation
    config: AskYourDocsConfig
    settings: ArmSettings


@dataclass
class _KeptTurnSink:
    """The question's trace sink, keeping the messages ``ask`` stamps into it for the record."""

    inner: ChatTraceSink
    messages: list[Any] = field(default_factory=list)

    async def stamp_turn(self, messages: Sequence[Any]) -> None:
        self.messages = list(messages)
        await self.inner.stamp_turn(messages)


def main(argv: Sequence[str]) -> int:
    """Run one arm; 0 when it completes, 2 when its inputs are refused (nothing spent)."""
    args = _parser().parse_args(list(argv))
    options = RunnerOptions(
        workspace=args.workspace,
        config=Path(args.config),
        llm_block=Path(args.llm_block),
        role=args.role,
        out=Path(args.out),
        split=args.split,
        max_agent_turns=args.max_agent_turns,
        model=args.model,
    )
    try:
        summary = asyncio.run(run_chat_arm(options))
    except (MeasurementPlanError, ChatDatasetError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{len(summary.tasks)} task(s) kept, {summary.excluded} excluded -> {options.out}")
    return 0


async def run_chat_arm(
    options: RunnerOptions,
    *,
    open_serve: Callable[..., Any] = page_serve_opener,
    build: Callable[..., Any] = build_agent_with_scope_capabilities,
) -> ArmSummary:
    """Ask every question of ``options.split`` once through ONE traced child; write the arm.

    ``open_serve`` / ``build`` are the two execution seams (the page's serve opener and
    the agent factory), so tests run the whole arm with named fakes and no model.
    """
    block = load_arm_llm_block(options.llm_block).settings
    config = _arm_config(options, block)
    tasks = await _tasks(options.split)
    trace = TraceLocation.minted_under(options.out.resolve() / _TRAJECTORIES_DIR)
    settings = _arm_settings(options, config, block, trace)
    opener = open_serve(options.workspace, str(options.config), subprocess_env=trace.child_env())
    async with opener(()) as held:
        built = await build(
            options.workspace,
            _chat_model(config),
            pydocs_config=str(options.config),
            config=config,
            mcp_tools=held.tools,
        )
        session = _ArmSession(built.graph, built.finalizer, held.tools, trace, config, settings)
        asked = await _ask_all(session, tasks)
    return _write_arm(options.out, session, asked)


def _arm_config(options: RunnerOptions, block: Mapping[str, object]) -> AskYourDocsConfig:
    """The served config with the arm's llm block as its ONLY model settings (rule P4)."""
    served = AppConfig.load(explicit_path=options.config).ask_your_docs
    model = options.model or _chat_model(served)
    if not model:
        raise MeasurementPlanError(
            f"no chat model: pass --model or set ask_your_docs.llm.model in {options.config}"
        )
    # The vision rule is a capability of the served endpoint, not a model setting.
    vision = {"vision": served.llm.vision} if served.llm and served.llm.vision is not None else {}
    llm = LlmConnectionConfig.model_validate({**block, "model": model, **vision})
    return served.model_copy(update={"llm": llm})


def _chat_model(config: AskYourDocsConfig) -> str | None:
    return config.llm.model if config.llm else None


def _arm_settings(
    options: RunnerOptions,
    config: AskYourDocsConfig,
    block: Mapping[str, object],
    trace: TraceLocation,
) -> ArmSettings:
    return ArmSettings(
        role=options.role,
        commit=options.commit or _checkout_head(options.role),
        workspace=options.workspace,
        model=_chat_model(config) or "",
        trace_root=str(trace.trace_root),
        out_dir=str(options.out),
        max_agent_turns=options.max_agent_turns or config.max_agent_turns,
        estimated_usd_per_rollout=0.0,
        cost_ceiling_usd=0.0,
        pydocs_config=str(options.config),
        llm_block=dict(block),
    )


def _checkout_head(role: ArmRole) -> str:
    """The HEAD of the checkout whose product this runner imports — the arm's ``commit``."""
    import pydocs_mcp

    sha, _subject = resolve_commit(Path(pydocs_mcp.__file__).resolve().parents[2], role, "HEAD")
    return sha


async def _tasks(split: str) -> list[EvalTask]:
    return [task async for task in dataset_registry.build(_DATASET, split=split).tasks()]


async def _ask_all(session: _ArmSession, tasks: Sequence[EvalTask]) -> list[ChatQuestionRun]:
    # Sequential on purpose: each question keeps only the capture lines recorded while
    # it ran, so two questions in flight would share (and split) one child's lines.
    return [await _ask(session, task) for task in tasks]


async def _ask(session: _ArmSession, task: EvalTask) -> ChatQuestionRun:
    """One question through ``ask``: its trajectory directory and its record."""
    sink = _KeptTurnSink(session.trace.question_sink(task.query, task.query))
    started = time.monotonic()
    error = await _answer(session, task.query, sink)
    last = sink.messages[-1] if sink.messages else None
    asked = ChatQuestionRun(
        task=task,
        trajectory_id=session.trace.trajectory_id,
        messages=tuple(sink.messages),
        question_dir=_newest_question_dir(session.trace),
        seconds=round(time.monotonic() - started, 1),
        error=error,
        budget_exhausted=last is not None and came_from_finalize(last),
    )
    merge_question_record(asked.question_dir, question_record(asked, session.settings))
    return asked


async def _answer(session: _ArmSession, question: str, sink: _KeptTurnSink) -> str | None:
    """Ask ``question`` as the chat page does; the error text when it failed, else None."""
    try:
        await ask(
            session.graph,
            [],
            question,
            finalizer=session.finalizer,
            on_event=_ignore_event,
            max_agent_turns=session.settings.max_agent_turns,
            seed_search=seeded_search_for(session.config.seed_search_with_question, session.tools),
            trace_sink=sink,
        )
    except Exception as exc:
        # WHY recorded, not raised: one failed question must not sink a paid arm. It is
        # kept in its question.json and left out of arm.json, counted as excluded.
        error = f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]
        print(f"question failed: {error}", file=sys.stderr)
        if not sink.messages:  # ask never reached its stamp: keep the question itself
            await sink.stamp_turn([HumanMessage(content=question)])
        return error
    return None


def _ignore_event(_event: object) -> None:
    """The activity sink that records nothing.

    WHY pass one at all: any sink selects the page's live (streamed) path, the one
    whose turn keeps the state a hand-built graph reached when it raises at its step
    limit — and the path the chat page itself takes by default.
    """
    return None


def _newest_question_dir(trace: TraceLocation) -> Path:
    """The directory the trace writer just wrote: the highest ``questions/<n>``."""
    numbered = [p for p in trace.questions_dir.iterdir() if p.name.isdigit()]
    return max(numbered, key=lambda path: int(path.name))


def _write_arm(out: Path, session: _ArmSession, asked: Sequence[ChatQuestionRun]) -> ArmSummary:
    # The campaign arm's own rule: a run without a recorded trace gets no row.
    kept = [a for a in asked if a.error is None and trace_recorded(a.trajectory_id, a.trace_dir)]
    settings = session.settings
    summary = ArmSummary(
        role=settings.role,
        commit=settings.commit,
        model=settings.model,
        trace_root=settings.trace_root,
        tasks=[record_of(a.task, a, settings) for a in kept],
        estimated_usd=0.0,
        halt_reason=HaltReason.COMPLETED,
        excluded=len(asked) - len(kept),
        max_agent_turns=settings.max_agent_turns,
    )
    out.mkdir(parents=True, exist_ok=True)
    write_arm_settings(out, settings)
    write_arm_summary(out, summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True, help="the bundle workspace to serve")
    parser.add_argument("--config", required=True, help="the serving YAML (embedder, agent)")
    parser.add_argument(
        "--llm-block", required=True, help="the arm's ask_your_docs.llm block (no model key)"
    )
    parser.add_argument("--role", required=True, type=ArmRole, choices=list(ArmRole))
    parser.add_argument("--out", required=True, help="the arm directory to write")
    parser.add_argument("--split", default=DEFAULT_CHAT_SPLIT, help="the chat slice to ask")
    parser.add_argument(
        "--max-agent-turns", type=int, default=None, help="the forced-cap instrument"
    )
    parser.add_argument("--model", default=None, help="overrides ask_your_docs.llm.model")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
