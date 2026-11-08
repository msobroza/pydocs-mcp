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

import asyncio
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from pydocs_eval.campaign.before_after_arm_records import (
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    ArmTaskRecord,
    read_arm_summary,
    write_arm_summary,
)
from pydocs_eval.campaign.before_after_arm_resume import ArmRunIdentity, rows_to_resume
from pydocs_eval.campaign.before_after_index_step import BundleIndexStep, refresh_bundle_indexes
from pydocs_eval.campaign.before_after_llm_block import block_turns_thinking_off
from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.ledger import CampaignLedger, WorkItem
from pydocs_eval.campaign.runner import RolloutOutcome, run_campaign
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.optimize._agent_track_binding import DEFAULT_TASK_TIMEOUT_SECONDS
from pydocs_eval.task_rendering import DEFAULT_TASK_SCAFFOLD, TaskScaffold
from pydocs_eval.trajectory.ask_outcome import (
    TaskOutcome,
    is_near_cap,
    outcome_of,
    recorded_answer,
    run_evidence,
)
from pydocs_eval.trajectory.server_capture import trace_recorded
from pydocs_eval.trajectory.token_accounting import last_finish_reason

# The settings an arm ran under, beside its summary (the arm child reads them back).
ARM_SETTINGS_FILENAME = "arm_settings.json"

log = logging.getLogger("pydocs-eval.campaign.before-after-arm")

# The structured event a rollout that RAISED emits, once per attempt. Named so a
# run.log can be grepped for the reason a run answered nothing.
_ROLLOUT_RAISED_EVENT = "before_after_rollout_raised"

# The structured event an arm emits when some of its tasks search the shared
# ``--workspace``: no source is known for it, so the index step cannot refresh
# it, and those tasks serve whatever trees it was built with (issue #380).
_SHARED_WORKSPACE_NOT_REINDEXED_EVENT = "before_after_shared_workspace_not_reindexed"

# One rollout at a time. The two arms already run sequentially (each needs the
# whole endpoint to itself for the comparison to be fair), and a burst of
# parallel rollouts would contend for the same serve child and endpoint.
_ARM_CONCURRENCY = 1

# The agent graph an arm answers with unless its settings overlay names another
# (``--baseline-settings`` / ``--candidate-settings``). Without overlays the two
# arms differ by the product commit and nothing else. Public because the
# plan-time block probe builds a runner the same way, and a second spelling of
# the default would make the probe answer about a setup no arm runs.
ARM_ARCHITECTURE = "text_react"


@dataclass(frozen=True, slots=True)
class ArmSettings:
    """Everything one arm needs that is NOT the code it runs under.

    ``task_workspaces`` maps a task id to the bundle directory holding ITS
    corpus. Both arms are given the same map (the plan builds it once), so the
    two arms retrieve from the same indexes and the report's difference is the
    product commit. A task missing from the map searches ``workspace``.

    ``bundle_sources`` maps each of those bundle directories to the corpus
    source it indexes; the arm re-indexes every one under its own product
    before serving (``before_after_index_step``, issue #380). Empty — an
    ``arm_settings.json`` written before the field, or a run with no corpora —
    means no index step, exactly as before.

    The last six fields are the arm's variant (issue #385): what its settings
    overlay decided beyond the commit. Their defaults are exactly what every arm
    ran before overlays existed, so an older ``arm_settings.json`` loads as it ran.
    ``settings_digest`` is ``""`` there — not recorded, never a match.
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
    bundle_sources: dict[str, str] = field(default_factory=dict)
    architecture: str = ARM_ARCHITECTURE
    # Merged under the runner settings' ``harness`` beside ``harness.llm``.
    harness_block: dict[str, object] = field(default_factory=dict)
    guidance_sections_path: str | None = None
    # The parsed sections of that file, as the plan validated them: the arm hands
    # these to every rollout, so a file edited after the plan cannot change a run.
    guidance_sections: dict[str, str] = field(default_factory=dict)
    task_scaffold: TaskScaffold = DEFAULT_TASK_SCAFFOLD
    settings_digest: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ArmSettings:
        """One ``arm_settings.json``; the scaffold read back as its enum."""
        fields = dict(payload)
        if "task_scaffold" in fields:
            fields["task_scaffold"] = TaskScaffold(str(fields["task_scaffold"]))
        return cls(**fields)  # type: ignore[arg-type]

    def run_identity(self) -> ArmRunIdentity:
        """What an earlier process's rows must match to be resumed into this arm."""
        return ArmRunIdentity(
            role=self.role,
            commit=self.commit,
            model=self.model,
            settings_digest=self.settings_digest,
        )

    def summary_variant_fields(self) -> dict[str, object]:
        """What the arm's ``arm.json`` records about this variant (``ArmSummary``'s fields)."""
        return {
            "architecture": self.architecture,
            "task_scaffold": str(self.task_scaffold),
            "guidance_sections_path": self.guidance_sections_path,
            "settings_digest": self.settings_digest,
        }

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


def build_product_harness_runner(
    settings: ArmSettings, workspace: Path, *, build: Callable[..., object] | None = None
) -> object:
    """The product harness runner one arm drives — the injection default.

    One runner per WORKSPACE, not per arm: the serve child it spawns is pinned
    to a single bundle directory, and a split spans several corpora. Built
    under the ARM's architecture and harness keys (its overlay), never a pin.

    Imported here and nowhere earlier: an arm that never runs (a plan-only
    invocation, a test) pays nothing for the agent runtime. ``build`` replaces
    ``build_ask_harness_runner`` in a test.
    """
    make = build if build is not None else _ask_harness_runner_builder()
    return make(
        workspace=workspace,
        model=settings.model,
        architecture=settings.architecture,
        max_agent_turns=settings.max_agent_turns,
        base_url=settings.base_url,
        pydocs_config=Path(settings.pydocs_config) if settings.pydocs_config else None,
        trace_root=settings.trace_root,
        task_timeout_seconds=settings.task_timeout_seconds,
        harness_llm=settings.llm_block,
        harness_block=settings.harness_block,
    )


def _ask_harness_runner_builder() -> Callable[..., object]:
    from pydocs_eval.optimize.ask_binding import build_ask_harness_runner

    return build_ask_harness_runner


def refresh_arm_bundles(settings: ArmSettings) -> tuple[BundleIndexStep, ...]:
    """Re-index this arm's bundles under THIS process's product — the default step.

    ``sys.executable`` with the inherited environment is the arm child's own
    interpreter and ``PYTHONPATH``, so the extraction runs the commit under test.
    """
    config = Path(settings.pydocs_config) if settings.pydocs_config else None
    return refresh_bundle_indexes(
        settings.bundle_sources, python=Path(sys.executable), config=config
    )


async def run_arm(
    settings: ArmSettings,
    tasks: Sequence[EvalTask],
    *,
    make_runner: Callable[[ArmSettings, Path], object] = build_product_harness_runner,
    refresh_indexes: Callable[[ArmSettings], tuple[BundleIndexStep, ...]] = refresh_arm_bundles,
) -> ArmSummary:
    """Re-index the arm's bundles, answer every task once under this process's
    product, and index the result.

    The re-index comes first and runs on every (re)start: a resumed arm finds
    its bundles already current, so the step is a cache hit that costs seconds.
    A restart also seeds the tasks an earlier process finished from its
    ``arm.json`` (``before_after_arm_resume``): the ledger will not run them again.
    """
    # Off the event loop: the step runs subprocesses and SQLite reads.
    index_steps = await asyncio.to_thread(refresh_indexes, settings)
    _log_shared_workspace_tasks(settings, tasks)
    out_dir = Path(settings.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    collector = _ArmRollouts(
        settings=settings,
        make_runner=make_runner,
        tasks=tasks,
        answered=rows_to_resume(out_dir, settings.run_identity()),
        index_steps=index_steps,
    )
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


def _log_shared_workspace_tasks(settings: ArmSettings, tasks: Sequence[EvalTask]) -> None:
    """Name the tasks the index step could not reach — they serve the shared workspace."""
    shared = [task.task_id for task in tasks if task.task_id not in settings.task_workspaces]
    if not shared:
        return
    log.warning(
        json.dumps(
            {
                "event": _SHARED_WORKSPACE_NOT_REINDEXED_EVENT,
                "role": settings.role,
                "workspace": settings.workspace,
                "task_ids": shared,
            }
        )
    )


def write_arm_settings(out_dir: Path, settings: ArmSettings) -> Path:
    """Persist the settings an arm runs under; return the file written."""
    path = out_dir / ARM_SETTINGS_FILENAME
    path.write_text(json.dumps(asdict(settings), indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_arm_settings(out_dir: Path) -> ArmSettings:
    """Load the settings an arm ran under, as :func:`write_arm_settings` wrote them.

    Example:
        >>> read_arm_settings(Path("runs/before-after/baseline")).workspace  # doctest: +SKIP
        '/home/me/pydocs-index'
    """
    payload = json.loads((out_dir / ARM_SETTINGS_FILENAME).read_text(encoding="utf-8"))
    return ArmSettings.from_dict(payload)


@dataclass(slots=True)
class _ArmRollouts:
    """Drives the harness once per work item and remembers where the trace landed."""

    settings: ArmSettings
    make_runner: Callable[[ArmSettings, Path], object]
    tasks: Sequence[EvalTask]
    answered: dict[str, ArmTaskRecord] = field(default_factory=dict)
    runners: dict[str, object] = field(default_factory=dict)
    index_steps: Sequence[BundleIndexStep] = ()

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
        if not trace_recorded(str(trajectory.trajectory_id), Path(trajectory.trace_dir)):
            return self._failed(f"no recorded trace (workspace {workspace})")
        self.answered[task.task_id] = record_of(task, trajectory, self.settings)
        # Before the ledger marks it DONE: a kill after this line loses no finished task.
        self._checkpoint()
        return RolloutOutcome(
            trajectory_id=trajectory.trajectory_id, cost_usd=self._booked(), is_infra=False
        )

    async def _trajectory_for(self, task: EvalTask, workspace: Path) -> RecordedRun:
        """One task, rendered as the run contract's sample and answered once.

        Whole-rollout scope on purpose: the guidance import, the sample render
        and the harness call can each fail, and :meth:`run` names whichever did.

        The sample's task name is the task's own framing, else ``repo_qa``
        (``sample_row_for_task``'s fallback): the binding resolves the guidance's
        task head by it, so a framed ``bug_loc`` task must never read ``repo_qa``.
        Its prompt is rendered under the ARM's scaffold: a bare arm sends the
        question alone, as the chat page does (issue #386).
        """
        from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task

        runner = self._runner_for(workspace)
        sample = sample_row_for_task(task, scaffold=self.settings.task_scaffold)
        guidance = dict(self.settings.guidance_sections)
        return await runner.run(sample, guidance)  # type: ignore[attr-defined,no-any-return]

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

    def _checkpoint(self) -> None:
        """Every row answered so far, on disk — what a restarted arm seeds from."""
        summary = self.summarize(halt_reason=HaltReason.IN_PROGRESS, excluded=0)
        write_arm_summary(Path(self.settings.out_dir), summary)

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
            index_steps=list(self.index_steps),
            **self.settings.summary_variant_fields(),  # type: ignore[arg-type]
        )


def record_of(task: EvalTask, trajectory: RecordedRun, settings: ArmSettings) -> ArmTaskRecord:
    """Index one finished task: where its trace is, its gold, and how the run ended.

    The ONE row builder: a campaign arm and the chat repro runner both write their
    ``arm.json`` rows through it, so the two read identically.
    """
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
        outcome=outcome_of_run(trajectory, settings),
        tool_calls=len(trajectory.server_tool_calls()),
        near_cap=is_near_cap(turns, max_agent_turns=settings.max_agent_turns),
    )


def outcome_of_run(trajectory: RecordedRun, settings: ArmSettings) -> TaskOutcome:
    """How this run ended, from what it returned and how its last reply finished."""
    evidence = run_evidence(
        trajectory,
        last_finish_reason=last_finish_reason(Path(trajectory.trace_dir)),
        thinking_off=block_turns_thinking_off(settings.llm_block),
    )
    return outcome_of(evidence)


# The record names are re-exported: they lived here before ``before_after_arm_records``.
__all__ = (
    "ARM_ARCHITECTURE",
    "ARM_SETTINGS_FILENAME",
    "ARM_SUMMARY_FILENAME",
    "ArmSettings",
    "ArmSummary",
    "ArmTaskRecord",
    "RecordedRun",
    "build_product_harness_runner",
    "outcome_of_run",
    "read_arm_settings",
    "read_arm_summary",
    "record_of",
    "refresh_arm_bundles",
    "run_arm",
    "write_arm_settings",
    "write_arm_summary",
)
