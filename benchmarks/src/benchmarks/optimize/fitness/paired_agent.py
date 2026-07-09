"""The ``paired_agent`` fitness — the one paid fitness of the v1 ladder (spec §D3).

Scores a candidate text artifact by running the slice-5 paired agent-track twice
over one split — once for the SEED (the baseline) and once for the candidate —
then comparing them per task on three efficiency metrics summed over BOTH arms:
context tokens (cache read + write), tool calls, and distinct files read. The
score is the weighted mean fractional reduction

    score = Σ weight_k · mean_over_tasks( (baseline_k − candidate_k) / max(baseline_k, _EPS) )

so a candidate that cuts the indexed arm's context/tool/file cost at answer
quality scores positive. A judge-parity PRE-GATE runs first (spec §D3): if the
candidate's blind judge mean drops more than ``judge_parity_floor`` below the
seed's, the score is ``-inf`` — a candidate that trades answer quality away for
efficiency is disqualified, never ranked.

The two threads a candidate can pull are carried by ``ArtifactInjection``:
``skill`` threads into every arm's task prompt (byte-identical to
``task_prompt(question, skill=...)``); ``overlay_path`` is carried for the arm-B
server command but is only WIRED by a later task — this fitness merely passes the
value through. The seed baseline is computed once per ``(seed.fingerprint,
split)`` and cached in-memory, so a run that scores many candidates pays for the
baseline exactly once.

Everything expensive is injected behind the agent-track Protocols (``runner`` /
``judge``), so the whole fitness is exercised offline with scripted doubles — no
subprocess, no socket, no live LLM (slice-6 contract).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from benchmarks.eval.datasets.base_dataset import Dataset, EvalTask
from benchmarks.optimize._agent_track_binding import (
    AgentTrackConfig,
    PairResult,
    RunMetrics,
    run_agent_track,
)
from benchmarks.optimize._split import partition_task_ids
from benchmarks.optimize._types import FitnessReport
from benchmarks.optimize.protocols import OptimizableArtifact
from benchmarks.optimize.registries import fitness_registry

# WHY: single source of truth for the weighting + parity floor. Bumping a weight
# touches one line here; the run-config YAML restates them for user clarity
# (YAML is exempt from the no-duplicate-literal rule).
_DEFAULT_WEIGHTS: Mapping[str, float] = {"tokens": 0.5, "tool_calls": 0.3, "files_read": 0.2}
_DEFAULT_PARITY_FLOOR = -0.25
_EPS = 1e-9

# Each efficiency metric summed over BOTH arms of one pair (spec §D3). The key is
# the weight key AND the ``<metric>_fraction`` component prefix, so the three stay
# in lockstep — adding a metric is one row plus one weight.
_METRIC_ACCESSORS: Mapping[str, Callable[[RunMetrics], float]] = {
    "tokens": lambda m: float(m.cache_read_tokens + m.cache_write_tokens),
    "tool_calls": lambda m: float(m.tool_calls),
    "files_read": lambda m: float(m.distinct_files_read),
}


@dataclass(frozen=True, slots=True)
class ArtifactInjection:
    """How a candidate artifact reaches the evaluated agent (spec §D3, §D6).

    ``skill`` is appended to every arm's task prompt (byte-identical to
    ``task_prompt(question, skill=...)``); ``overlay_path`` is the arm-B server
    overlay file — carried here but wired into the server command by a later
    task, so this fitness only threads the value through.
    """

    skill: str = ""
    overlay_path: Path | None = None


@fitness_registry.register("paired_agent")
@dataclass
class PairedAgentFitness:
    """Paired agent-track fitness with a judge-parity pre-gate (spec §D3).

    Not frozen: it caches the seed baseline in ``_baseline_cache`` so a run that
    scores many candidates computes the baseline once. Everything else is
    injected and immutable.
    """

    runner: object  # AgentRunner Protocol (structural; kept off the binding here)
    judge: object  # Judge Protocol (structural)
    dataset: Dataset
    ledger_path: Path
    agent_cfg: AgentTrackConfig
    seed_artifact: OptimizableArtifact
    inject: Callable[[OptimizableArtifact], ArtifactInjection]
    weights: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    judge_parity_floor: float = _DEFAULT_PARITY_FLOOR

    name: str = "paired_agent"
    cost_tier: Literal["free", "paid"] = "paid"

    # WHY: the baseline is identical across every candidate scored against the
    # same seed + split, so it is computed once and reused (keyed by the seed's
    # content fingerprint and the split) — the run pays for the baseline exactly
    # once, not once per candidate.
    _baseline_cache: dict[tuple[str, str], tuple[PairResult, ...]] = field(
        default_factory=dict, repr=False
    )

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport:
        """Score ``artifact`` on ``split`` vs the seed baseline (spec §D3).

        Runs the seed pass once (cached) and the candidate pass, pairs them by
        ``task_id``, applies the judge-parity pre-gate, and returns the weighted
        fractional-reduction score plus every raw mean + fraction.
        """
        split_ids = await self._split_task_ids(split)
        seed_pairs, seed_cost = await self._baseline_pairs(split, split_ids)
        cand_pairs, cand_cost = await self._run_pass(artifact, split, split_ids)
        paired = _pair_by_task_id(seed_pairs, cand_pairs)
        cost = seed_cost + cand_cost
        if not paired:
            # No comparable task: an empty score is honest (nothing was measured)
            # rather than a fabricated zero-reduction win.
            return FitnessReport(score=0.0, components={}, cost_usd=cost, n_samples=0)
        return _build_report(paired, weights=self.weights, floor=self.judge_parity_floor, cost=cost)

    async def _split_task_ids(self, split: str) -> frozenset[str]:
        """Collect the dataset's task ids and return the requested split's set.

        ``partition_task_ids`` fires its loud non-empty-split guard HERE on the
        real path (not only in dry-run): a task pool too small or skewed to fill
        both sides is a config error, not a silent skew (spec §D3).
        """
        ids = [task.task_id async for task in self.dataset.tasks()]
        train, holdout = partition_task_ids(ids)
        return frozenset(train if split == "train" else holdout)

    async def _baseline_pairs(
        self, split: str, split_ids: frozenset[str]
    ) -> tuple[tuple[PairResult, ...], float]:
        """The seed pass, computed once per ``(seed.fingerprint, split)``.

        A cache hit returns the stored pairs and zero cost (nothing spent this
        call); a miss runs the seed pass, caches it, and returns its actual cost.
        """
        key = (self.seed_artifact.fingerprint, split)
        cached = self._baseline_cache.get(key)
        if cached is not None:
            return cached, 0.0
        pairs, cost = await self._run_pass(self.seed_artifact, split, split_ids)
        self._baseline_cache[key] = pairs
        return pairs, cost

    async def _run_pass(
        self, artifact: OptimizableArtifact, split: str, split_ids: frozenset[str]
    ) -> tuple[tuple[PairResult, ...], float]:
        """Run ``run_agent_track`` once for ``artifact`` over ``split_ids``.

        The candidate's ``skill`` is threaded into every arm's prompt through a
        skill-appending runner wrapper (byte-identical to ``task_prompt(...,
        skill=...)``). A per-fingerprint ledger keeps the seed and each candidate
        pass from cross-contaminating the agent-track resume set.
        """
        injection = self.inject(artifact)
        runner = _SkillAppendingRunner(inner=self.runner, skill=injection.skill)
        dataset = _SplitDataset(inner=self.dataset, keep=split_ids)
        ledger = self._pass_ledger(artifact.fingerprint, split)
        pairs = await run_agent_track(
            self.agent_cfg,
            dataset=dataset,
            runner=runner,
            judge=self.judge,
            ledger_path=ledger,
        )
        return pairs, _pass_cost(pairs)

    def _pass_ledger(self, fingerprint: str, split: str) -> Path:
        # Sibling of the configured trials ledger, keyed by fingerprint + split so
        # the seed pass and each candidate pass own disjoint agent-track resume
        # state (no cross-contamination of the done-task set).
        base = self.ledger_path
        return base.with_name(f"{base.stem}.{fingerprint[:12]}.{split}{base.suffix}")


@dataclass(frozen=True, slots=True)
class _SkillAppendingRunner:
    """Wraps an ``AgentRunner`` to append the candidate skill to every prompt.

    ``run_agent_track`` renders each arm's prompt with ``task_prompt(question)``
    (no skill). Appending ``\\n\\n{skill}`` here is byte-identical to
    ``task_prompt(question, skill=skill)`` (that helper appends exactly this when
    the skill is non-empty), so the injected skill reaches BOTH arms without the
    orchestrator needing a skill hook. An empty skill is a pass-through.
    """

    inner: object
    skill: str

    async def run(
        self,
        arm: object,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        threaded = f"{prompt}\n\n{self.skill}" if self.skill else prompt
        return await self.inner.run(arm, prompt=threaded, cwd=cwd, mcp_config=mcp_config)


@dataclass(frozen=True, slots=True)
class _SplitDataset:
    """Wraps a ``Dataset`` to yield only the tasks whose id is in ``keep``.

    Lets the fitness run the agent track over exactly one split's tasks while
    leaving the underlying dataset (and its per-task ``corpus_source`` closures)
    untouched.
    """

    inner: Dataset
    keep: frozenset[str]

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def revision(self) -> str:
        return self.inner.revision

    def tasks(self) -> AsyncIterator[EvalTask]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[EvalTask]:
        async for task in self.inner.tasks():
            if task.task_id in self.keep:
                yield task


def _pass_cost(pairs: tuple[PairResult, ...]) -> float:
    """Total arm spend across one pass's admitted pairs (both arms per pair)."""
    total = 0.0
    for pair in pairs:
        # PairResult.__post_init__ guarantees both arms; narrow for the type checker.
        assert pair.bare is not None
        assert pair.indexed is not None
        total += pair.bare.cost_usd + pair.indexed.cost_usd
    return total


def _pair_by_task_id(
    seed: tuple[PairResult, ...], candidate: tuple[PairResult, ...]
) -> tuple[tuple[PairResult, PairResult], ...]:
    """Pair seed and candidate results by ``task_id`` (only tasks in BOTH)."""
    by_id = {p.task_id: p for p in candidate}
    return tuple((s, by_id[s.task_id]) for s in seed if s.task_id in by_id)


def _metric_sum(pair: PairResult, accessor: Callable[[RunMetrics], float]) -> float:
    """One efficiency metric summed over BOTH arms of a pair (spec §D3)."""
    # PairResult.__post_init__ guarantees both arms; narrow for the type checker.
    assert pair.bare is not None
    assert pair.indexed is not None
    return accessor(pair.bare) + accessor(pair.indexed)


def _judge_mean(pair: PairResult) -> float:
    """The pair's blind judge mean (indexed arm) — 0.0 when the judge is absent."""
    return pair.judge.mean if pair.judge is not None else 0.0


def _build_report(
    paired: tuple[tuple[PairResult, PairResult], ...],
    *,
    weights: Mapping[str, float],
    floor: float,
    cost: float,
) -> FitnessReport:
    """Assemble the score, components, and pre-gate outcome for ``paired``."""
    n = len(paired)
    judge_delta = sum(_judge_mean(c) - _judge_mean(s) for s, c in paired) / n
    components: dict[str, float] = {"judge_mean_delta": judge_delta}
    if judge_delta < floor:
        # Judge-parity pre-gate (spec §D3): a candidate that drops answer quality
        # past the floor is disqualified — ``-inf`` so the ladder never ranks it.
        return FitnessReport(score=float("-inf"), components=components, cost_usd=cost, n_samples=n)
    score = 0.0
    for metric, accessor in _METRIC_ACCESSORS.items():
        base_mean = sum(_metric_sum(s, accessor) for s, _ in paired) / n
        cand_mean = sum(_metric_sum(c, accessor) for _, c in paired) / n
        fraction = _fraction(base_mean, cand_mean)
        components[f"{metric}_baseline"] = base_mean
        components[f"{metric}_candidate"] = cand_mean
        components[f"{metric}_fraction"] = fraction
        score += weights.get(metric, 0.0) * fraction
    return FitnessReport(score=score, components=components, cost_usd=cost, n_samples=n)


def _fraction(baseline: float, candidate: float) -> float:
    """Fractional reduction ``(baseline − candidate) / max(baseline, _EPS)``.

    ``_EPS`` guards the zero-baseline denominator so a metric with no baseline
    activity contributes a bounded (near-zero) fraction rather than dividing by
    zero.
    """
    return (baseline - candidate) / max(baseline, _EPS)
