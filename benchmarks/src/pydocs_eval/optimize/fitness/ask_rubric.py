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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask
from pydocs_eval.datasets.task_ids import parse_framed_task_id, record_id_of
from pydocs_eval.optimize._agent_track_binding import DEFAULT_RNG_SEED
from pydocs_eval.optimize._prefix_report import task_id_prefix
from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import _DEFAULT_MAX_JUDGE_CALLS, FitnessReport
from pydocs_eval.optimize.arm_scoring import observe_tracked_metrics
from pydocs_eval.optimize.ask_binding import (
    ask_binding_identity,
    guidance_sections_for_candidate,
    known_task_names,
)
from pydocs_eval.optimize.fitness._ask_rubric_report import build_fitness_report
from pydocs_eval.optimize.multitask.sampling import BatchSampler, UniformSampler
from pydocs_eval.optimize.orchestrator import BudgetExhausted
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import fitness_registry
from pydocs_eval.optimize.rubric.checks import deterministic_checks, score_checks
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

# The task name a v1 row falls back to when nothing else names one: no arm
# declaration and no framed task id (run-contract design §5's pre-framing
# corpora). ``repo_qa`` is the majority framing after the 2026-07-28 taxonomy
# consolidation — three of the four shipped corpora (swe-qa-pro, repoqa-qa,
# swe-qa-questions) mint under it, and every un-armed config is a QA config.
_DEFAULT_TASK_NAME = "repo_qa"


@lru_cache(maxsize=1)
def enumerated_task_names() -> tuple[str, ...]:
    """The product's v1 task names, read ONCE per process.

    The vocabulary :func:`parse_framed_task_id` anchors on — cached because it
    is consulted per sample and the answer is a product constant. This module
    already hard-requires ``pydocs_mcp`` on the scoring path (through
    ``ask_binding_identity``), so no absent-product fallback is owed here.
    """
    return known_task_names()


def sample_row_for_task(task: EvalTask, *, task_name: str = "") -> dict[str, object]:
    """The run contract's sample row for one eval task (design §2 rule 6, §5).

    Carries ``REQUIRED_SAMPLE_KEYS``: ``record_id``, ``task_name``,
    ``rendered_prompt`` (the SHARED task scaffold — which the ask path did not
    previously apply, the verdict-moving half of the stage-3 measurement bump),
    and ``gold``.

    Both identity keys prefer EXPLICIT sources over parsing, in this order:

    - ``record_id`` — delegated to :func:`record_id_of`, the ONE reader the
      record-keyed split also uses: the task's own ``record_id`` field (what a
      framing layer sets, and what multi-framing siblings share), else the
      record segment of a three-part ``<dataset>/<task_name>/<record_id>`` id,
      else the task id.
    - ``task_name`` — the ARM's declared framing (the only value validated
      against the product loader's enumerated set), else the framing segment of
      a three-part id, else :data:`_DEFAULT_TASK_NAME`. There is deliberately
      NO dataset-prefix step: a prefix is a CORPUS namespace, not a framing, and
      after the 2026-07-28 consolidation no shipped prefix (``ccv``,
      ``sweqapro``, ``repoqa-qa``, ``swe-qa-questions``) is an enumerated task
      name — so that step could only ever produce a value the product's
      ``task_head_section_header`` raises on. Mapping prefixes to framings here
      would mint a second spelling of the taxonomy that must stay in sync with
      the registry (the reason ``arms.dataset`` refuses prefix aliases).

    Example:
        >>> sorted(sample_row_for_task(task))  # doctest: +SKIP
        ['gold', 'record_id', 'rendered_prompt', 'task_name']
    """
    names = enumerated_task_names()
    framed = parse_framed_task_id(task.task_id, task_names=names)
    # NEVER a second derivation: ``record_id_of`` is what ``_split_tasks``
    # partitions on, so re-deriving the record here is how the split unit and
    # the harness/ledger unit silently become two different strings.
    record_id = record_id_of(task, task_names=names)
    resolved_name = task_name or (framed.task_name if framed else "") or _DEFAULT_TASK_NAME
    return {
        "record_id": record_id,
        "task_name": resolved_name,
        "rendered_prompt": render_task_prompt(task.query),
        "gold": task.gold,
    }


def _ledger_record_id(task: EvalTask, record_id: str) -> str:
    """The ledger's ``record_id``: empty when the record IS the row's task id.

    Mirrors ``EvalTask.record_id`` / ``task_ids.record_id_of`` so one rule
    holds on both sides, and keeps a pre-framing sample line byte-identical to
    what the previous writer produced (``sample_ledger._as_line`` drops the
    empty field).
    """
    return "" if record_id == task.task_id else record_id


def verdict_when_judge_skipped(rubric: RubricConfig, gate_pass_fraction: float) -> float:
    """The verdict for a sample whose judge fail_fast skipped.

    Historically 0.0 — defensible while gates were pure screens, since a failed
    gate meant "do not score this". Once the deterministic layer carries a graded
    score that cliff discards real measurement: a sample satisfying most of its
    checks scored identically to one satisfying none, after the harness had
    already paid to compute both.

    With ``keep_deterministic_on_skip`` the deterministic layer still counts, but
    only for its own weight — the rubric weight is unearned, never redistributed
    — so a skipped-judge verdict is capped at ``gate_weight``.

    That cap is NOT a strict ordering against judged samples, and this docstring
    used to claim it was. Once the deterministic layer carries scored checks its
    composite comes from the CHECKS, not from the gate outcomes, so a sample that
    trips a screen can still compose high: at ``gate_weight`` 0.5, a skipped
    sample composing 1.0 lands at 0.5 — above a judged sample composing 0.75 with
    a rubric score of 0.2 (0.475). What holds is the cap: a skipped sample can
    never reach the top of the ladder, because generation's weight stays
    unearned.
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
    #: The arm's declared ``task_name``; ``""`` falls back to a framed id's
    #: framing segment, then to :data:`_DEFAULT_TASK_NAME` (see
    #: :func:`sample_row_for_task`).
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
        return build_fitness_report(
            records,
            fresh_cost=fresh_cost,
            configured_criteria=tuple(c.name for c in self.rubric.criteria),
        )

    async def _split_tasks(self, split: str) -> tuple[EvalTask, ...]:
        """The requested split's tasks in seeded deterministic order.

        Partitioned on the RECORD id, never the row's task id (platform spec
        §5.4 / run-contract §10 finding 4): every framing minted from one
        record must land on the same side, or two rows of one record straddle
        train and holdout and the split leaks. Byte-identical for every
        pre-framing corpus, whose ``record_id`` defaults to its task id.
        """
        tasks = [task async for task in self.dataset.tasks()]
        records = [record_id_of(task, task_names=enumerated_task_names()) for task in tasks]
        train, _holdout = partition_task_ids(records)
        train_records = set(train)
        wants_train = split == "train"
        selected = [
            task
            for task, record in zip(tasks, records, strict=True)
            if (record in train_records) == wants_train
        ]
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
        sample = sample_row_for_task(task, task_name=self.task_name)
        trajectory = await runner.run(sample, guidance_sections)
        # ONE deterministic pass over gates AND scored checks. For a gates-only
        # objective this is exactly the old ``fmean`` over booleans (each gate
        # weighs 1.0 and fails below a full pass), so no shipped verdict moves;
        # see ``checks.deterministic_checks`` for the role rule.
        deterministic = score_checks(
            deterministic_checks(self.rubric.gates, self.rubric.checks), task, trajectory
        )
        outcomes = deterministic.outcomes
        gates = {g.name: outcomes[g.name].passed for g in self.rubric.gates}
        check_scores = {
            c.name: outcomes[c.name].score for c in self.rubric.checks if c.name in outcomes
        }
        gate_pass_fraction = deterministic.score
        judge_skipped = self.rubric.fail_fast and deterministic.blocked
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
            checks=check_scores,
            arm_hash=self.arm_hash,
            # The SAME value the harness was handed, never a second derivation:
            # the ledger's clustering unit must be the row's own record.
            record_id=_ledger_record_id(task, str(sample["record_id"])),
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
            "checks": dict(record.checks),
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


def _safe_filename(task_id: str) -> str:
    """Task ids may carry path separators / spaces; keep the file name flat."""
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in task_id)
