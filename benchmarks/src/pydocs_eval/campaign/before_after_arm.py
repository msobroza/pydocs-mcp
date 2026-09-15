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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.ledger import CampaignLedger, WorkItem
from pydocs_eval.campaign.runner import RolloutOutcome, run_campaign
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize._agent_track_binding import DEFAULT_TASK_TIMEOUT_SECONDS

ARM_SUMMARY_FILENAME = "arm.json"

# One rollout at a time. The two arms already run sequentially (each needs the
# whole endpoint to itself for the comparison to be fair), and a burst of
# parallel rollouts would contend for the same serve child and endpoint.
_ARM_CONCURRENCY = 1

# The agent graph both arms answer with. Pinned, not configurable: the two arms
# must differ by the product commit and nothing else.
_ARM_ARCHITECTURE = "text_react"


@dataclass(frozen=True, slots=True)
class ArmSettings:
    """Everything one arm needs that is NOT the code it runs under."""

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


@dataclass(frozen=True, slots=True)
class ArmTaskRecord:
    """Where one answered task's evidence landed, plus its gold."""

    task_id: str
    trajectory_id: str
    trace_dir: str
    gold_files: list[str]
    turns: int
    wall_seconds: float
    answer_chars: int


@dataclass(frozen=True, slots=True)
class ArmSummary:
    """One arm's result index — what the report reads instead of re-running."""

    role: str
    commit: str
    model: str
    trace_root: str
    tasks: list[ArmTaskRecord]
    estimated_usd: float
    halt_reason: str
    excluded: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ArmSummary:
        tasks = [ArmTaskRecord(**dict(row)) for row in payload.get("tasks", [])]  # type: ignore[arg-type]
        return cls(
            role=str(payload["role"]),
            commit=str(payload["commit"]),
            model=str(payload["model"]),
            trace_root=str(payload["trace_root"]),
            tasks=tasks,
            estimated_usd=float(payload["estimated_usd"]),
            halt_reason=str(payload["halt_reason"]),
            excluded=int(payload["excluded"]),
        )


def read_arm_summary(out_dir: Path) -> ArmSummary:
    """Load the summary an arm process wrote into ``out_dir``."""
    payload = json.loads((out_dir / ARM_SUMMARY_FILENAME).read_text(encoding="utf-8"))
    return ArmSummary.from_dict(payload)


def build_product_harness_runner(settings: ArmSettings) -> object:
    """The product harness runner one arm drives — the injection default.

    Imported here and nowhere earlier: an arm that never runs (a plan-only
    invocation, a test) pays nothing for the agent runtime.
    """
    from pydocs_eval.optimize.ask_binding import build_ask_harness_runner

    return build_ask_harness_runner(
        workspace=Path(settings.workspace),
        model=settings.model,
        architecture=_ARM_ARCHITECTURE,
        max_agent_turns=settings.max_agent_turns,
        base_url=settings.base_url,
        pydocs_config=Path(settings.pydocs_config) if settings.pydocs_config else None,
        trace_root=settings.trace_root,
        task_timeout_seconds=settings.task_timeout_seconds,
    )


async def run_arm(
    settings: ArmSettings,
    tasks: Sequence[EvalTask],
    *,
    make_runner: Callable[[ArmSettings], object] = build_product_harness_runner,
) -> ArmSummary:
    """Answer every task once under this process's product, and index the result."""
    collector = _ArmRollouts(settings=settings, runner=make_runner(settings), tasks=tasks)
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
    runner: object
    tasks: Sequence[EvalTask]
    answered: dict[str, ArmTaskRecord] = field(default_factory=dict)

    async def run(self, item: WorkItem) -> RolloutOutcome:
        """One task through the harness; a traceless result is an infra outcome."""
        from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task

        task = self._task(item.instance_id)
        trajectory = await self.runner.run(sample_row_for_task(task), {})  # type: ignore[attr-defined]
        if not trajectory.trajectory_id:
            return RolloutOutcome(
                trajectory_id="", cost_usd=self._booked(), is_infra=True, completed=False
            )
        self.answered[task.task_id] = _record_of(task, trajectory)
        return RolloutOutcome(
            trajectory_id=trajectory.trajectory_id, cost_usd=self._booked(), is_infra=False
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
        )


def _record_of(task: EvalTask, trajectory: object) -> ArmTaskRecord:
    """Index one answered task: where its trace is, and what its gold was."""
    return ArmTaskRecord(
        task_id=task.task_id,
        trajectory_id=str(trajectory.trajectory_id),  # type: ignore[attr-defined]
        trace_dir=str(trajectory.trace_dir),  # type: ignore[attr-defined]
        gold_files=list(task.gold.file_set),
        turns=int(trajectory.turns),  # type: ignore[attr-defined]
        wall_seconds=float(trajectory.wall_seconds),  # type: ignore[attr-defined]
        answer_chars=len(str(trajectory.answer)),  # type: ignore[attr-defined]
    )
