"""The offline stand-ins a ``--dry-run`` walks on — nothing here can spend.

Split out of ``dry_run`` so the walk itself reads as the sequence of preflight
steps it is. Every object in this module is a scripted double: a free fitness,
a no-op optimizer, a synthetic dataset, a canned trajectory, and the
runner/judge pair a dry rubric pass scores against. None of them reaches an
LLM, a subprocess, a socket or the agent runtime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# WHY unguarded: only the dry-run walk imports this module, and that walk
# already requires the library-coupled optimize layer.
from pydocs_mcp.harness.platform.contract import (
    ToolCallObservation,
    ToolCallRecord,
    Trajectory,
)

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
)
from pydocs_eval.optimize.ask_binding import FakeAskRunner
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.orchestrator import SeedView
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.rubric.judge import FakeRubricJudge
from pydocs_eval.optimize.run_config import AskRubricSettings

# A tiny synthetic id sample the split-determinism check runs over when the
# config names no fixture — enough distinct ids that the sha256 % 2 predicate
# lands on BOTH sides so ``partition_task_ids`` proves it is deterministic and
# non-empty without any network (a real run resolves the dataset's own ids).
SPLIT_PROBE_IDS = tuple(f"swe-qa-pro:{i:04d}" for i in range(12))

# The scripted judge's per-criterion score — high enough to exercise the whole
# weighted-verdict path, and never compared against a threshold.
_PROBE_JUDGE_SCORE = 8.0


@dataclass(frozen=True, slots=True)
class ZeroCostFitness:
    """A free, zero-spend fitness for the dry-run orchestrator pass.

    Scores every candidate 0.0 on both splits at no cost, so the full
    orchestrator pass (seed validate → train firewall → holdout gate) runs
    end-to-end spending nothing. Never used on a real run — the real ladder
    resolves the paid ``paired_agent`` fitness.
    """

    name: str = "paired_agent"
    cost_tier: Literal["free", "paid"] = "free"

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        _ = (artifact, split)  # dry-run: nothing is measured, nothing is spent
        return FitnessReport(score=0.0, components={}, cost_usd=0.0, n_samples=0)


@dataclass(frozen=True, slots=True)
class SeedEchoOptimizer:
    """A no-op optimizer for the dry-run pass: returns the seed, proposes nothing.

    Drives the orchestrator's gate + train-firewall wiring without an LLM or a
    subprocess (the real optimizers reach a client / ``train.py``). ``best=None``
    means "nothing beat the seed", so the pass exercises the whole control loop
    at zero spend.
    """

    name: str = "dry-run-echo"

    async def optimize(
        self,
        seed: SeedView,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        _ = (ladder, budget)  # the orchestrator owns the gate; this proposes nothing
        return OptimizationResult(
            best=None,
            accepted=False,
            trials=(),
            total_usd=0.0,
            provenance=seed.provenance,
        )


@dataclass(slots=True)
class ProbeDataset:
    """Synthetic offline tasks over the split-probe ids (dry rubric pass)."""

    name: str = "dry-run-probe"
    revision: str = "0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for task_id in SPLIT_PROBE_IDS:
            yield EvalTask(
                task_id=task_id,
                query=task_id,
                gold=GoldAnswer(),
                corpus_source=lambda: None,  # type: ignore[arg-type]
            )


def dry_trajectory(task_id: str) -> Trajectory:
    """One scripted trajectory that passes the shipped gates at $0.00.

    The tool call is SERVER-observed so the ``used_indexed_tools`` gate — which
    reads the trace-derived slice (run-contract design §3) — sees it.
    """
    return Trajectory(
        trajectory_id=f"dry-{task_id}",
        trace_dir=Path(),
        answer=f"dry-run probe answer for {task_id} " + "x" * 40,
        tool_calls=(
            ToolCallRecord(
                tool_name="search_codebase",
                args_digest="dry",
                observed_by=ToolCallObservation.SERVER,
            ),
        ),
        turns=2,
        cost_usd=0.0,
        wall_seconds=0.1,
    )


def dry_ask_doubles(rubric: AskRubricSettings) -> tuple[FakeAskRunner, FakeRubricJudge]:
    """The scripted runner + judge one dry rubric pass runs on (AC-17).

    Trajectories pass the section's gates and the judge scores every criterion
    of ``rubric``, so the pass walks the whole gate → judge → verdict →
    sample-ledger path at $0.00 — with no harness runtime and no live judge
    anywhere in it. One pair PER PASS, so each arm's ``runner.calls`` /
    ``judge.calls`` report that arm's own work rather than a running total.

    WHY the SECTION and not the whole run config: an arm is scored against the
    section its ``scoring.rubric`` resolves to, and the day a config carries a
    second named objective the top-level ``ask_rubric:`` criteria are the WRONG
    names — ``AskRubricFitness._score_sample`` indexes ``criteria[c.name]`` and
    would raise inside the preflight a paid run depends on.
    """
    trajectories = {task_id: dry_trajectory(task_id) for task_id in SPLIT_PROBE_IDS}
    scores = {
        task_id: {c.name: _PROBE_JUDGE_SCORE for c in rubric.criteria}
        for task_id in SPLIT_PROBE_IDS
    }
    return FakeAskRunner(scripted=trajectories), FakeRubricJudge(scripted=scores)
