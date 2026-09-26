"""One arm of a before/after run: the split, answered under ONE product commit.

An arm is a whole process, not a coroutine, because the thing under test is the
imported product: two commits of ``pydocs_mcp`` cannot live in one interpreter,
and the serve child inherits its parent's ``PYTHONPATH``. The parent
(``before_after_command``) checks each commit out into a git worktree and spawns
this module's entry point with that worktree first on the path, so the harness,
the prompts and the MCP server all come from the commit under test.

The loop itself is the campaign runner's: this module supplies the work items,
the ledger and the budget guard, and a rollout function that drives the product
harness once. That buys resume for free — a killed arm re-runs only the tasks
that never reached a terminal state.

**Spend the harness cannot see.** The in-process ask harness reports
``cost_usd = 0.0`` by contract (its endpoint is often local or internal and
quotes no price), so a ceiling checked against reported spend would never fire.
Each rollout is therefore booked at the plan's estimated per-rollout cost, which
makes ``--max-usd`` a real stop and is recorded in the arm summary as an
estimate, never as a measurement.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from pydocs_eval.campaign.before_after_llm_block import block_turns_thinking_off
from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.ledger import CampaignLedger, WorkItem
from pydocs_eval.campaign.runner import RolloutOutcome, run_campaign
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize._agent_track_binding import DEFAULT_TASK_TIMEOUT_SECONDS
from pydocs_eval.trajectory.ask_outcome import (
    UNKNOWN_TURN_BUDGET,
    TaskOutcome,
    is_near_cap,
    outcome_of,
    recorded_answer,
    run_evidence,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME
from pydocs_eval.trajectory.token_accounting import last_finish_reason

ARM_SUMMARY_FILENAME = "arm.json"

log = logging.getLogger("pydocs-eval.campaign.before-after-arm")

# The structured event a rollout that RAISED emits, once per attempt. Named so a
# run.log can be grepped for the reason a run answered nothing.
_ROLLOUT_RAISED_EVENT = "before_after_rollout_raised"

# One rollout at a time. The two arms already run sequentially (each needs the
# whole endpoint to itself for the comparison to be fair), and a burst of
# parallel rollouts would contend for the same serve child and endpoint.
_ARM_CONCURRENCY = 1

# The agent graph both arms answer with. Pinned, not configurable: the two arms
# must differ by the product commit and nothing else. Public because the
# plan-time block probe builds a runner the same way, and a second spelling of
# the architecture would make the probe answer about a setup no arm runs.
ARM_ARCHITECTURE = "text_react"


@dataclass(frozen=True, slots=True)
class ArmSettings:
    """Everything one arm needs that is NOT the code it runs under.

    ``task_workspaces`` maps a task id to the bundle directory holding ITS
    corpus. Both arms are given the same map (the plan builds it once), so the
    two arms retrieve from the same indexes and the report's difference is the
    product commit. A task missing from the map searches ``workspace``.
    """

    role: str
    commit: str
    workspace: str
    model: str
    trace_root: str
    out_dir: str
    max_agent_turns: int
    estimated_usd_per_rollout: float
    cost_ceiling_usd: float
    base_url: str | None = None
    pydocs_config: str | None = None
    task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS
    # The ``ask_your_docs.llm`` block this arm pins (``--llm-block``), or None.
    # Model settings are arm-side by contract: the binding refuses them from the
    # serving file, so this mapping — identical in both arms — is the only channel.
    llm_block: dict[str, object] | None = None
    task_workspaces: dict[str, str] = field(default_factory=dict)

    def workspace_for(self, task_id: str) -> Path:
        """The bundle directory ``task_id`` searches — its corpus, else the shared one."""
        return Path(self.task_workspaces.get(task_id, self.workspace))


class RecordedRun(Protocol):
    """What an arm reads off one finished harness run.

    The product run contract's ``Trajectory`` satisfies it structurally — this
    module never imports the product, whose version is the thing under test.
    The outcome flags a product declares from issue #371 on are read with
    ``getattr`` defaults (``trajectory.ask_outcome.run_evidence``).
    """

    @property
    def trajectory_id(self) -> str: ...
    @property
    def trace_dir(self) -> Path: ...
    @property
    def answer(self) -> str: ...
    @property
    def turns(self) -> int: ...
    @property
    def wall_seconds(self) -> float: ...
    def server_tool_calls(self) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class ArmTaskRecord:
    """Where one answered task's evidence landed, its gold, and how the run ended.

    The last four fields are defaulted so an ``arm.json`` written before they
    existed still loads: its rows read ``UNRECORDED``, and the measurement
    back-fills what a legacy row can still tell (``ask_outcome.legacy_outcome_of``).
    ``tool_calls`` is ``None`` when not recorded — undefined, never "no calls".
    ``answer`` never holds LangGraph's canned apology (``recorded_answer``).
    """

    task_id: str
    trajectory_id: str
    trace_dir: str
    gold_files: list[str]
    turns: int
    wall_seconds: float
    answer_chars: int
    answer: str = ""
    outcome: TaskOutcome = TaskOutcome.UNRECORDED
    tool_calls: int | None = None
    near_cap: bool = False

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> ArmTaskRecord:
        """One ``arm.json`` row; an unknown outcome is refused by name.

        A row without an ``outcome`` keeps the field's own default.
        """
        fields = dict(row)
        if "outcome" in fields:
            fields["outcome"] = _task_outcome(fields["outcome"])
        return cls(**fields)  # type: ignore[arg-type]


def _task_outcome(value: object) -> TaskOutcome:
    """``value`` as a :class:`TaskOutcome`, or a ValueError naming it and the vocabulary."""
    try:
        return TaskOutcome(str(value))
    except ValueError:
        accepted = ", ".join(outcome.value for outcome in TaskOutcome)
        raise ValueError(f"arm.json outcome {value!r} is not one of: {accepted}") from None


@dataclass(frozen=True, slots=True)
class ArmSummary:
    """One arm's result index — what the report reads instead of re-running.

    ``max_agent_turns`` is the budget this arm ran under, so its outcomes can be
    read against its OWN cap; ``UNKNOWN_TURN_BUDGET`` for an ``arm.json`` written
    before the field.
    """

    role: str
    commit: str
    model: str
    trace_root: str
    tasks: list[ArmTaskRecord]
    estimated_usd: float
    halt_reason: str
    excluded: int
    max_agent_turns: int = UNKNOWN_TURN_BUDGET

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ArmSummary:
        rows: Sequence[Mapping[str, object]] = payload.get("tasks", [])  # type: ignore[assignment]
        return cls(
            role=str(payload["role"]),
            commit=str(payload["commit"]),
            model=str(payload["model"]),
            trace_root=str(payload["trace_root"]),
            tasks=[ArmTaskRecord.from_dict(row) for row in rows],
            estimated_usd=float(payload["estimated_usd"]),
            halt_reason=str(payload["halt_reason"]),
            excluded=int(payload["excluded"]),
            max_agent_turns=int(payload.get("max_agent_turns", UNKNOWN_TURN_BUDGET)),  # type: ignore[call-overload]
        )


def read_arm_summary(out_dir: Path) -> ArmSummary:
    """Load the summary an arm process wrote into ``out_dir``."""
    payload = json.loads((out_dir / ARM_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    return ArmSummary.from_dict(payload)


def build_product_harness_runner(settings: ArmSettings, workspace: Path) -> object:
    """The product harness runner one arm drives — the injection default.

    One runner per WORKSPACE, not per arm: the serve child it spawns is pinned
    to a single bundle directory, and a split spans several corpora.

    Imported here and nowhere earlier: an arm that never runs (a plan-only
    invocation, a test) pays nothing for the agent runtime.
    """
    from pydocs_eval.optimize.ask_binding import build_ask_harness_runner

    return build_ask_harness_runner(
        workspace=workspace,
        model=settings.model,
        architecture=ARM_ARCHITECTURE,
        max_agent_turns=settings.max_agent_turns,
        base_url=settings.base_url,
        pydocs_config=Path(settings.pydocs_config) if settings.pydocs_config else None,
        trace_root=settings.trace_root,
        task_timeout_seconds=settings.task_timeout_seconds,
        harness_llm=settings.llm_block,
    )


async def run_arm(
    settings: ArmSettings,
    tasks: Sequence[EvalTask],
    *,
    make_runner: Callable[[ArmSettings, Path], object] = build_product_harness_runner,
) -> ArmSummary:
    """Answer every task once under this process's product, and index the result."""
    collector = _ArmRollouts(settings=settings, make_runner=make_runner, tasks=tasks)
    out_dir = Path(settings.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = await run_campaign(
        # One cell per arm: the ledger's ``(cell, instance)`` key then keeps the
        # two arms' rows apart even when they share one file.
        [WorkItem(cell=settings.role, instance_id=t.task_id) for t in tasks],
        ledger=CampaignLedger(path=out_dir / "queue.jsonl"),
        guard=BudgetGuard(
            cost_ceiling_usd=settings.cost_ceiling_usd,
            assumed_cost_on_raise=settings.estimated_usd_per_rollout,
        ),
        rollout_fn=collector.run,
        concurrency=_ARM_CONCURRENCY,
    )
    summary = collector.summarize(halt_reason=str(result.halt_reason), excluded=result.excluded)
    write_arm_summary(out_dir, summary)
    return summary


def write_arm_summary(out_dir: Path, summary: ArmSummary) -> Path:
    """Persist the arm summary; return the file written."""
    path = out_dir / ARM_SUMMARY_FILENAME
    path.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


@dataclass(slots=True)
class _ArmRollouts:
    """Drives the harness once per work item and remembers where the trace landed."""

    settings: ArmSettings
    make_runner: Callable[[ArmSettings, Path], object]
    tasks: Sequence[EvalTask]
    answered: dict[str, ArmTaskRecord] = field(default_factory=dict)
    runners: dict[str, object] = field(default_factory=dict)

    async def run(self, item: WorkItem) -> RolloutOutcome:
        """One task through ITS workspace's harness; a traceless result is infra.

        A rollout that RAISES is an infra outcome too, but it carries its cause:
        timeouts and turn-budget overruns come back as sentinel trajectories, so
        anything that raises here is a real failure (a refused config, a dead
        endpoint) and the whole run will repeat it. Booking is unchanged — the
        plan's estimate, exactly what the guard's raise backstop would book.

        A run is COMPLETE — booked once, never retried — only when its trace
        file is on disk: an exhausted or timed-out run that kept its trace is
        measured under its outcome, one that lost it is infra exactly as before.
        """
        task = self._task(item.instance_id)
        workspace = self.settings.workspace_for(task.task_id)
        try:
            trajectory = await self._trajectory_for(task, workspace)
        except Exception as exc:
            return self._raised(item, exc)
        if not _trace_recorded(trajectory):
            return self._failed(f"no recorded trace (workspace {workspace})")
        self.answered[task.task_id] = _record_of(task, trajectory, self.settings)
        return RolloutOutcome(
            trajectory_id=trajectory.trajectory_id, cost_usd=self._booked(), is_infra=False
        )

    async def _trajectory_for(self, task: EvalTask, workspace: Path) -> RecordedRun:
        """One task, rendered as the run contract's sample and answered once.

        Whole-rollout scope on purpose: the guidance import, the sample render
        and the harness call can each fail, and :meth:`run` names whichever did.
        """
        from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task

        runner = self._runner_for(workspace)
        return await runner.run(sample_row_for_task(task), {})  # type: ignore[attr-defined]

    def _runner_for(self, workspace: Path) -> object:
        """The runner serving ``workspace``, built once and reused by its tasks."""
        key = str(workspace)
        if key not in self.runners:
            self.runners[key] = self.make_runner(self.settings, workspace)
        return self.runners[key]

    def _raised(self, item: WorkItem, exc: BaseException) -> RolloutOutcome:
        """Record WHY one rollout failed — in the queue's detail and in the log.

        The workspace rides along: a split spans several corpora, so "which
        index was this rollout searching" is half of what a post-mortem needs.
        """
        workspace = self.settings.workspace_for(item.instance_id)
        detail = f"{type(exc).__name__}: {exc} (workspace {workspace})"
        log.error(
            json.dumps(
                {
                    "event": _ROLLOUT_RAISED_EVENT,
                    "role": self.settings.role,
                    "task_id": item.instance_id,
                    "workspace": str(workspace),
                    "error": detail,
                }
            )
        )
        return self._failed(detail)

    def _failed(self, detail: str) -> RolloutOutcome:
        """A costed infra outcome — the booking the guard's raise backstop makes."""
        return RolloutOutcome(
            trajectory_id="",
            cost_usd=self._booked(),
            is_infra=True,
            completed=False,
            detail=detail,
        )

    def _booked(self) -> float:
        """What this rollout charges the ceiling — the plan's estimate, not a price."""
        return self.settings.estimated_usd_per_rollout

    def _task(self, task_id: str) -> EvalTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"work item names task {task_id!r}, which this arm does not carry")

    def summarize(self, *, halt_reason: str, excluded: int) -> ArmSummary:
        ordered = [self.answered[t.task_id] for t in self.tasks if t.task_id in self.answered]
        return ArmSummary(
            role=self.settings.role,
            commit=self.settings.commit,
            model=self.settings.model,
            trace_root=self.settings.trace_root,
            tasks=ordered,
            estimated_usd=len(ordered) * self._booked(),
            halt_reason=halt_reason,
            excluded=excluded,
            max_agent_turns=self.settings.max_agent_turns,
        )


def _trace_recorded(trajectory: RecordedRun) -> bool:
    """True when the run has an id AND its events file is really on disk.

    Checked on the FILE: ``Path()`` — the trace dir of a run that has none — is
    the working directory, which always exists.
    """
    if not str(trajectory.trajectory_id):
        return False
    return (Path(trajectory.trace_dir) / SERVER_EVENTS_FILENAME).is_file()


def _record_of(task: EvalTask, trajectory: RecordedRun, settings: ArmSettings) -> ArmTaskRecord:
    """Index one finished task: where its trace is, its gold, and how the run ended."""
    turns = int(trajectory.turns)
    answer = recorded_answer(str(trajectory.answer))
    return ArmTaskRecord(
        task_id=task.task_id,
        trajectory_id=str(trajectory.trajectory_id),
        trace_dir=str(trajectory.trace_dir),
        gold_files=list(task.gold.file_set),
        turns=turns,
        wall_seconds=float(trajectory.wall_seconds),
        answer_chars=len(answer),
        answer=answer,
        outcome=_outcome_of_run(trajectory, settings),
        tool_calls=len(trajectory.server_tool_calls()),
        near_cap=is_near_cap(turns, max_agent_turns=settings.max_agent_turns),
    )


def _outcome_of_run(trajectory: RecordedRun, settings: ArmSettings) -> TaskOutcome:
    """How this run ended, from what it returned and how its last reply finished."""
    evidence = run_evidence(
        trajectory,
        last_finish_reason=last_finish_reason(Path(trajectory.trace_dir)),
        thinking_off=block_turns_thinking_off(settings.llm_block),
    )
    return outcome_of(evidence)
