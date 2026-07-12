"""The ``ask_rubric`` paid fitness — layered gate → rubric → verdict (spec §3.4.4).

For each task in the requested split: resume from the sample ledger (free),
else run the candidate's ask agent, evaluate the deterministic gates (free),
judge the survivors (paid, bounded by ``max_judge_calls``), compose the
weighted verdict, and persist one ``SampleRubricRecord`` plus a per-sample
transcript file — every low-scoring ledger line has an inspectable transcript
behind it.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Literal

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask
from pydocs_eval.optimize._agent_track_binding import _DEFAULT_RNG_SEED
from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import _DEFAULT_MAX_JUDGE_CALLS, FitnessReport
from pydocs_eval.optimize.ask_binding import AskRunner, AskTranscript
from pydocs_eval.optimize.orchestrator import BudgetExhausted
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import fitness_registry
from pydocs_eval.optimize.rubric.gates import evaluate_gate
from pydocs_eval.optimize.rubric.judge import RubricJudge
from pydocs_eval.optimize.rubric.model import (
    RubricConfig,
    SampleRubricRecord,
    rubric_config_hash,
)
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger

# Judge scores are 0-10; the rubric score normalizes to 0-1 (spec §3.4.4).
_JUDGE_SCALE = 10.0


@fitness_registry.register("ask_rubric")
@dataclass(slots=True)
class AskRubricFitness:
    """Paid per-sample rubric fitness over the headless ask agent.

    ``runner_factory`` maps the candidate artifact to its runner (prompt /
    architecture / overlay injection happens there), so this fitness stays
    axis-agnostic. The judge-call counter spans the whole run — the
    ``max_judge_calls`` ceiling is enforced predictively (a call that would
    exceed it never starts, AC-14) and ``BudgetExhausted`` stops the
    orchestrator gracefully.
    """

    dataset: Dataset
    runner_factory: Callable[[OptimizableArtifact], AskRunner]
    judge: RubricJudge
    rubric: RubricConfig
    architecture: str
    sample_ledger: SampleRubricLedger
    output_dir: Path
    max_judge_calls: int = _DEFAULT_MAX_JUDGE_CALLS
    rng_seed: int = _DEFAULT_RNG_SEED
    name: str = "ask_rubric"
    cost_tier: Literal["free", "paid"] = "paid"
    _judge_calls: int = field(default=0, init=False)

    def objective_hash(self) -> str:
        """The objective identity both ledgers key on (spec §3.6)."""
        return rubric_config_hash(self.rubric, architecture=self.architecture)

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        """Score ``artifact`` on ``split``, per sample, resuming from the ledger."""
        tasks = await self._split_tasks(split)
        runner = self.runner_factory(artifact)
        records: list[SampleRubricRecord] = []
        fresh_cost = 0.0
        for task in tasks:
            hit = self.sample_ledger.lookup(
                fingerprint=artifact.fingerprint,
                split=split,
                task_id=task.task_id,
                objective_hash=self.objective_hash(),
            )
            if hit is not None:
                records.append(hit)
                continue
            record = await self._score_sample(artifact, task, split=split, runner=runner)
            fresh_cost += record.cost_usd
            records.append(record)
        return _report(records, fresh_cost=fresh_cost)

    async def _split_tasks(self, split: str) -> tuple[EvalTask, ...]:
        """The requested split's tasks in seeded deterministic order."""
        tasks = [task async for task in self.dataset.tasks()]
        train, _holdout = partition_task_ids([t.task_id for t in tasks])
        keep = set(train) if split == "train" else set(t.task_id for t in tasks) - set(train)
        selected = [t for t in tasks if t.task_id in keep]
        # WHY seeded shuffle: task order decides WHICH samples a budget
        # cutoff reaches; seeding pins it so a resumed run replays the order.
        random.Random(self.rng_seed).shuffle(selected)
        return tuple(selected)

    async def _score_sample(
        self,
        artifact: OptimizableArtifact,
        task: EvalTask,
        *,
        split: str,
        runner: AskRunner,
    ) -> SampleRubricRecord:
        """Run → gates → (judge) → verdict → persist, for ONE sample."""
        transcript = await runner.run(task.query)
        gates = {g.name: evaluate_gate(g, task, transcript) for g in self.rubric.gates}
        gate_pass_fraction = fmean(gates.values()) if gates else 1.0
        judge_skipped = self.rubric.fail_fast and not all(gates.values())
        criteria: dict[str, float] = {}
        rubric_score = 0.0
        judge_cost = 0.0
        discarded: str | None = None
        if not judge_skipped and self.rubric.criteria:
            self._check_judge_budget()
            self._judge_calls += 1
            verdict = await self.judge.score(
                question=task.query, answer=transcript.answer, criteria=self.rubric.criteria
            )
            judge_cost = verdict.cost_usd
            if verdict.scores is None:
                discarded = verdict.discard_reason or "judge reply unusable"
            else:
                criteria = dict(verdict.scores)
                rubric_score = sum(
                    c.weight * criteria[c.name] / _JUDGE_SCALE for c in self.rubric.criteria
                )
        verdict_score = (
            0.0
            if judge_skipped
            else self.rubric.gate_weight * gate_pass_fraction
            + self.rubric.rubric_weight * rubric_score
        )
        record = SampleRubricRecord(
            fingerprint=artifact.fingerprint,
            split=split,
            task_id=task.task_id,
            qa_type=str(task.metadata.get("qa_type", "")),
            objective_hash=self.objective_hash(),
            gates=gates,
            gate_pass_fraction=gate_pass_fraction,
            judge_skipped=judge_skipped,
            criteria=criteria,
            rubric_score=rubric_score,
            verdict=verdict_score,
            turns=transcript.turns,
            wall_seconds=transcript.wall_seconds,
            cost_usd=transcript.cost_usd + judge_cost,
            answer_sha256=hashlib.sha256(transcript.answer.encode()).hexdigest(),
            discarded=discarded,
        )
        self.sample_ledger.record(record)
        self._write_transcript(record, task, transcript)
        return record

    def _check_judge_budget(self) -> None:
        """Predictive ceiling: the call that would exceed it never starts (AC-14)."""
        if self._judge_calls + 1 > self.max_judge_calls:
            raise BudgetExhausted(
                f"max_judge_calls {self.max_judge_calls} would be exceeded: "
                f"{self._judge_calls} judge call(s) already made this run"
            )

    def _write_transcript(
        self, record: SampleRubricRecord, task: EvalTask, transcript: AskTranscript
    ) -> None:
        """The per-sample inspection file behind every ledger line (spec §3.4.5)."""
        directory = self.output_dir / "samples" / record.fingerprint[:12]
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task.task_id,
            "question": task.query,
            "answer": transcript.answer,
            "tool_calls": [[c.tool_name, c.args_digest] for c in transcript.tool_calls],
            "turns": transcript.turns,
            "wall_seconds": transcript.wall_seconds,
            "gates": dict(record.gates),
            "criteria": dict(record.criteria),
            "verdict": record.verdict,
            "discarded": record.discarded,
        }
        path = directory / f"{_safe_filename(task.task_id)}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _report(records: list[SampleRubricRecord], *, fresh_cost: float) -> FitnessReport:
    """Aggregate sample records into the ladder-facing report (AC-13)."""
    admitted = [r for r in records if r.discarded is None]
    score = fmean(r.verdict for r in admitted) if admitted else -math.inf
    components: dict[str, float] = {
        "gate_pass_rate": fmean(r.gate_pass_fraction for r in records) if records else 0.0,
        "judge_skip_rate": fmean(float(r.judge_skipped) for r in records) if records else 0.0,
        "judge_calls": float(
            sum(1 for r in records if not r.judge_skipped and (r.criteria or r.discarded))
        ),
        "discards": float(len(records) - len(admitted)),
        "turns_mean": fmean(r.turns for r in admitted) if admitted else 0.0,
        "wall_seconds_mean": fmean(r.wall_seconds for r in admitted) if admitted else 0.0,
    }
    components.update(_criterion_means(admitted))
    components.update(_gate_rates(records))
    return FitnessReport(
        score=score, components=components, cost_usd=fresh_cost, n_samples=len(admitted)
    )


def _criterion_means(admitted: list[SampleRubricRecord]) -> dict[str, float]:
    names = sorted({name for r in admitted for name in r.criteria})
    return {
        f"criterion.{name}_mean": fmean(r.criteria[name] for r in admitted if name in r.criteria)
        for name in names
    }


def _gate_rates(records: list[SampleRubricRecord]) -> dict[str, float]:
    names = sorted({name for r in records for name in r.gates})
    return {
        f"gate.{name}_rate": fmean(float(r.gates[name]) for r in records if name in r.gates)
        for name in names
    }


def _safe_filename(task_id: str) -> str:
    """Task ids may carry path separators / spaces; keep the file name flat."""
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in task_id)
