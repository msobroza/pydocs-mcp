"""The ``ask_rubric`` paid fitness — layered gate → rubric → verdict (spec §3.4.4).

For each task in the requested split: resume from the sample ledger (free),
else run the candidate's ask agent, evaluate the deterministic gates (free),
judge the survivors (paid, bounded by ``max_judge_calls``), compose the
weighted verdict, and persist one ``SampleRubricRecord`` plus a per-sample
trajectory file — every low-scoring ledger line has an inspectable trajectory
behind it.

The harness is driven through the product run contract (run-contract design
§2): a conformant sample row in, one ``Trajectory`` out. The candidate itself
travels as ``guidance_sections`` — the optimizer's named-section view — so
this fitness stays agnostic about which physical representation (prompts,
skill, doc sections) carries the text.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Literal

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask
from pydocs_eval.optimize._agent_track_binding import DEFAULT_RNG_SEED
from pydocs_eval.optimize._prefix_report import task_id_prefix
from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import _DEFAULT_MAX_JUDGE_CALLS, FitnessReport
from pydocs_eval.optimize.arm_scoring import observe_tracked_metrics
from pydocs_eval.optimize.ask_binding import (
    ask_binding_identity,
    guidance_sections_for_candidate,
)
from pydocs_eval.optimize.multitask.sampling import BatchSampler, UniformSampler
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
from pydocs_eval.task_rendering import render_task_prompt

if TYPE_CHECKING:
    # WHY TYPE_CHECKING: ``ask_binding`` already declares the product
    # coupling at runtime; naming the contract type here is a typing concern.
    from pydocs_mcp.harness.core.run_contract import HarnessRunner, Trajectory

# Judge scores are 0-10; the rubric score normalizes to 0-1 (spec §3.4.4).
_JUDGE_SCALE = 10.0

# How many hash characters name a per-candidate / per-arm inspection directory.
_DIR_HASH_CHARS = 12

# The task name a v1 row falls back to when a task_id carries no dataset
# prefix (run-contract design §5: each corpus mints exactly one framing today
# and its name doubles as the record namespace).
_DEFAULT_TASK_NAME = "sweqapro"


def sample_row_for_task(task: EvalTask, *, task_name: str = "") -> dict[str, object]:
    """The run contract's sample row for one eval task (design §2 rule 6, §5).

    Carries ``REQUIRED_SAMPLE_KEYS``: ``record_id`` (the task id, which is
    also its record namespace while each corpus mints one framing),
    ``task_name``, ``rendered_prompt`` (the SHARED task scaffold — which the
    ask path did not previously apply, the verdict-moving half of the stage-3
    measurement bump), and ``gold``.

    ``task_name`` is the ARM's declared framing when an arm supplies one — the
    only value validated against the product loader's enumerated set. Falling
    back to the dataset prefix is correct only for corpora whose ids ARE
    prefixed: a single-dataset crosscommitvuln run yields bare ids like
    ``cve-2025-10283``, whose "prefix" is the whole id, and the product's
    ``task_head_section_header`` raises on any name outside ``TASK_NAMES``.

    Example:
        >>> sorted(sample_row_for_task(task))  # doctest: +SKIP
        ['gold', 'record_id', 'rendered_prompt', 'task_name']
    """
    return {
        "record_id": task.task_id,
        "task_name": task_name or task_id_prefix(task.task_id) or _DEFAULT_TASK_NAME,
        "rendered_prompt": render_task_prompt(task.query),
        "gold": task.gold,
    }


def verdict_when_judge_skipped(rubric: RubricConfig, gate_pass_fraction: float) -> float:
    """The verdict for a sample whose judge fail_fast skipped.

    Historically 0.0 — defensible while gates were pure screens, since a failed
    gate meant "do not score this". Once the deterministic layer carries a graded
    score that cliff discards real measurement: a sample satisfying most of its
    checks scored identically to one satisfying none, after the harness had
    already paid to compute both.

    With ``keep_deterministic_on_skip`` the deterministic layer still counts, but
    only for its own weight — the rubric weight is unearned, not redistributed —
    so a skipped-judge sample can never match a judged one.
    """
    if not rubric.keep_deterministic_on_skip:
        return 0.0
    return rubric.gate_weight * gate_pass_fraction


def ask_objective_hash(rubric: RubricConfig, *, architecture: str) -> str:
    """THE objective identity of the ask-rubric objective — one value, two folds.

    Both things that key on "which objective produced this number" fold this
    exact string: the sample ledger, through
    :meth:`AskRubricFitness.objective_hash`, and ARM identity, through
    ``run_config.arm_objective_hash`` → ``ArmCell.fingerprint``.

    WHY one function (owner directive 2026-07-27, review catch): two spellings
    silently diverge. Bumping ``TASK_SCAFFOLD_VERSION`` or the gate observation
    source moves ``ask_binding_identity``, so every sample line correctly
    re-runs — but an arm hash that folded a binding-free rubric hash would stay
    byte-identical and keep resuming arm-keyed rows measured under the OLD
    execution path. That is exactly the silent reuse design §8 forbids.

    Not free of the harness: ``ask_binding_identity`` imports the product
    binding for its delivery-map digest — the same import ``fingerprint``'s
    ``delivery_map_hash`` input already makes — which is why the load-time arm
    firewall resolves the objective by NAME and mints this value later.
    """
    return rubric_config_hash(
        rubric, architecture=architecture, binding_identity=ask_binding_identity()
    )


@dataclass(slots=True)
class JudgeCallCounter:
    """Fresh judge calls made this RUN — ONE counter shared by every arm.

    WHY a shared object rather than a per-fitness ``int``: ``max_judge_calls``
    is a RUN ceiling, and each arm builds its own ``AskRubricFitness``. A
    per-instance counter would silently multiply the ceiling by the number of
    arms — a two-arm run would buy 2 x 200 judge calls under a config that
    says 200. The default factory keeps a lone fitness exactly as it was.
    """

    calls: int = 0


@fitness_registry.register("ask_rubric")
@dataclass(slots=True)
class AskRubricFitness:
    """Paid per-sample rubric fitness over the headless ask agent.

    ``runner_factory`` maps the candidate artifact to its ``HarnessRunner``
    (settings / architecture / overlay binding happens there), so this fitness
    stays axis-agnostic; the candidate's TEXT reaches the agent as
    ``guidance_sections`` on each run. The judge-call counter spans the whole
    run — the ``max_judge_calls`` ceiling is enforced predictively (a call that
    would exceed it never starts, AC-14) and ``BudgetExhausted`` stops the
    orchestrator gracefully.
    """

    dataset: Dataset
    runner_factory: Callable[[OptimizableArtifact], HarnessRunner]
    judge: RubricJudge
    rubric: RubricConfig
    architecture: str
    sample_ledger: SampleRubricLedger
    output_dir: Path
    # WHY per-run counter: the ceiling bounds ONE process's fresh judge
    # calls; resumed samples are free, so a rerun only counts new spend.
    max_judge_calls: int = _DEFAULT_MAX_JUDGE_CALLS
    rng_seed: int = DEFAULT_RNG_SEED
    #: How the split is ORDERED before the budget cutoff truncates it. Defaults
    #: to uniform — byte-identical to the seeded shuffle this replaced.
    sampler: BatchSampler = field(default_factory=UniformSampler)
    #: The arm's OBSERVATIONAL metric names — its ``scoring.tracked`` cell key
    #: (run-contract design §6). Measured per sample and recorded beside the
    #: verdict; they never enter it, and they are NOT in ``objective_hash``.
    #: Empty by default, byte-identical to the single-implicit-arm behavior;
    #: ``arm_runtime.build_arm_fitness`` supplies the per-arm value.
    tracked_metrics: tuple[str, ...] = ()
    #: WHICH ARM this fitness scores for — stamped on every sample line and
    #: part of the sample-ledger resume key (run-contract design §6). ``""`` is
    #: the single implicit arm a config without an ``arms:`` block runs.
    arm_hash: str = ""
    #: The arm's declared ``task_name``; ``""`` falls back to the task id's
    #: dataset prefix (see :func:`sample_row_for_task`).
    task_name: str = ""
    #: The RUN's fresh-judge-call counter. Every arm of one run is handed the
    #: SAME instance so ``max_judge_calls`` stays one pool.
    judge_calls: JudgeCallCounter = field(default_factory=JudgeCallCounter)
    name: str = "ask_rubric"
    cost_tier: Literal["free", "paid"] = "paid"

    def objective_hash(self) -> str:
        """The objective identity both ledgers key on (spec §3.6).

        Folds the ask execution path's identity (scaffold version, delivery
        map, gate observation source) so stage 3's measurement bump lands as
        ONE recorded objective change (run-contract design §8). Delegates to
        :func:`ask_objective_hash` — arm identity folds that same value, and a
        second spelling here is how the two silently drift apart.
        """
        return ask_objective_hash(self.rubric, architecture=self.architecture)

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        """Score ``artifact`` on ``split``, per sample, resuming from the ledger."""
        tasks = await self._split_tasks(split)
        runner = self.runner_factory(artifact)
        # The candidate's text, projected ONCE per pass into the contract's
        # named-section mapping (design §4). Non-sectioned families yield {}
        # and ride the runner settings / serve overlay instead.
        guidance_sections = guidance_sections_for_candidate(artifact)
        records: list[SampleRubricRecord] = []
        fresh_cost = 0.0
        for task in tasks:
            hit = self.sample_ledger.lookup(
                fingerprint=artifact.fingerprint,
                split=split,
                task_id=task.task_id,
                objective_hash=self.objective_hash(),
                arm_hash=self.arm_hash,
            )
            if hit is not None and hit.discarded is None:
                records.append(hit)
                continue
            # WHY discards re-run: a discard is a judge FAILURE (timeout /
            # malformed reply), not a score — resuming it forever would make
            # a transient judge hiccup permanent. Re-paying one judge call is
            # bounded by max_judge_calls.
            record = await self._score_sample(
                artifact,
                task,
                split=split,
                runner=runner,
                guidance_sections=guidance_sections,
            )
            fresh_cost += record.cost_usd
            records.append(record)
        return _report(
            records,
            fresh_cost=fresh_cost,
            configured_criteria=tuple(c.name for c in self.rubric.criteria),
        )

    async def _split_tasks(self, split: str) -> tuple[EvalTask, ...]:
        """The requested split's tasks in seeded deterministic order."""
        tasks = [task async for task in self.dataset.tasks()]
        train, _holdout = partition_task_ids([t.task_id for t in tasks])
        keep = set(train) if split == "train" else set(t.task_id for t in tasks) - set(train)
        selected = [t for t in tasks if t.task_id in keep]
        # WHY the sampler: task order decides WHICH samples a budget cutoff
        # reaches, and the cutoff takes a PREFIX. Under the default uniform
        # sampler this is the seeded shuffle it has always been (identical
        # output for a given seed). A stratified sampler instead puts every
        # dataset at the head, so a truncated combined run cannot silently
        # admit zero rows from the smaller member.
        return tuple(
            self.sampler.order(
                selected, self.rng_seed, key=lambda task: task_id_prefix(task.task_id)
            )
        )

    async def _score_sample(
        self,
        artifact: OptimizableArtifact,
        task: EvalTask,
        *,
        split: str,
        runner: HarnessRunner,
        guidance_sections: Mapping[str, str],
    ) -> SampleRubricRecord:
        """Run → gates → (judge) → verdict → persist, for ONE sample."""
        trajectory = await runner.run(
            sample_row_for_task(task, task_name=self.task_name), guidance_sections
        )
        gates = {g.name: evaluate_gate(g, task, trajectory) for g in self.rubric.gates}
        gate_pass_fraction = fmean(gates.values()) if gates else 1.0
        judge_skipped = self.rubric.fail_fast and not all(gates.values())
        criteria: dict[str, float] = {}
        rubric_score = 0.0
        judge_cost = 0.0
        discarded: str | None = None
        if not judge_skipped and self.rubric.criteria:
            self._check_judge_budget()
            self.judge_calls.calls += 1
            verdict = await self.judge.score(
                question=task.query, answer=trajectory.answer, criteria=self.rubric.criteria
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
            verdict_when_judge_skipped(self.rubric, gate_pass_fraction)
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
            turns=trajectory.turns,
            wall_seconds=trajectory.wall_seconds,
            cost_usd=trajectory.cost_usd + judge_cost,
            answer_sha256=hashlib.sha256(trajectory.answer.encode()).hexdigest(),
            discarded=discarded,
            tracked=observe_tracked_metrics(self.tracked_metrics, task=task, trajectory=trajectory),
            arm_hash=self.arm_hash,
        )
        self.sample_ledger.record(record)
        self._write_trajectory_file(record, task, trajectory)
        return record

    def _check_judge_budget(self) -> None:
        """Predictive ceiling: the call that would exceed it never starts (AC-14)."""
        if self.judge_calls.calls + 1 > self.max_judge_calls:
            raise BudgetExhausted(
                f"max_judge_calls {self.max_judge_calls} would be exceeded: "
                f"{self.judge_calls.calls} judge call(s) already made this run"
            )

    def _write_trajectory_file(
        self, record: SampleRubricRecord, task: EvalTask, trajectory: Trajectory
    ) -> None:
        """The per-sample inspection file behind every ledger line (spec §3.4.5).

        ``trajectory_id`` is the join key back to the ADR 0009 server trace,
        and each tool call carries its observation point, so a reader can tell
        a server-recorded call from a harness-local one without re-deriving it.
        """
        directory = self.output_dir / "samples" / self._sample_dir_name(record.fingerprint)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task.task_id,
            "trajectory_id": trajectory.trajectory_id,
            "question": task.query,
            "answer": trajectory.answer,
            "tool_calls": [
                [c.tool_name, c.args_digest, str(c.observed_by)] for c in trajectory.tool_calls
            ],
            "turns": trajectory.turns,
            "wall_seconds": trajectory.wall_seconds,
            "gates": dict(record.gates),
            "criteria": dict(record.criteria),
            "verdict": record.verdict,
            "discarded": record.discarded,
        }
        path = directory / f"{_safe_filename(task.task_id)}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _sample_dir_name(self, fingerprint: str) -> str:
        """The per-candidate inspection directory, disambiguated per arm.

        WHY the arm segment: two arms of one run score the SAME candidate on
        the SAME task ids, so a candidate-only directory made the second arm
        silently overwrite the first arm's inspection files — the low-scoring
        ledger line would point at another arm's trajectory. Absent an arm
        (the single implicit one) the path is byte-identical to before.
        """
        short = fingerprint[:_DIR_HASH_CHARS]
        return f"{short}-{self.arm_hash[:_DIR_HASH_CHARS]}" if self.arm_hash else short


def _report(
    records: list[SampleRubricRecord],
    *,
    fresh_cost: float,
    configured_criteria: tuple[str, ...] = (),
) -> FitnessReport:
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
    components.update(_criterion_means(admitted, configured_criteria))
    components.update(_gate_rates(records))
    return FitnessReport(
        score=score, components=components, cost_usd=fresh_cost, n_samples=len(admitted)
    )


def _criterion_means(
    admitted: list[SampleRubricRecord], configured: tuple[str, ...]
) -> dict[str, float]:
    # WHY the configured union: AC-13 promises EVERY criterion.<name>_mean —
    # a rung where every sample was gate-skipped still reports the keys
    # (0.0) instead of silently dropping them.
    names = sorted(set(configured) | {name for r in admitted for name in r.criteria})
    return {
        f"criterion.{name}_mean": (
            fmean(r.criteria[name] for r in admitted if name in r.criteria)
            if any(name in r.criteria for r in admitted)
            else 0.0
        )
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
