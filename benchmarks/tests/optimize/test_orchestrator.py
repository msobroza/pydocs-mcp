"""Acceptance gate + orchestrator contract (plan Task 8, spec §D4).

Drives ``run_optimization`` with a fake fitness (scriptable train/holdout
scores) and a fake optimizer that echoes one candidate. No agent-track code,
no subprocess, no live LLM — the whole gate is exercised offline. The tests
pin the D4 acceptance rules: real margin (``0.02``), non-finite seed aborts
(never auto-accepts), non-finite candidate is rejected-but-reported, the
optimizer's fitness is physically train-bound, the outer budget stops the
search gracefully, and a run's output carries the human-landable diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import pytest

from pydocs_eval.optimize._types import (
    OptimizationBudget,
    OptimizationResult,
    Provenance,
)
from pydocs_eval.optimize.ladder import FitnessLadder, Rung
from pydocs_eval.optimize.orchestrator import (
    _ACCEPT_MARGIN,
    SeedView,
    run_optimization,
)
from pydocs_eval.optimize.trials_ledger import TrialsLedger

# The rung fitness name every fake ladder in this module references.
_FITNESS_NAME = "fake"


# --------------------------------------------------------------------------- #
# Offline doubles
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _TextArtifact:
    """A minimal artifact whose render() IS its text (already firewall-clean)."""

    text: str
    name: str = "usage_skill"

    def render(self) -> str:
        return self.text

    def with_content(self, content: str) -> _TextArtifact:
        return replace(self, text=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()


@dataclass
class _FakeReport:
    """A stand-in ``FitnessReport`` carrying only what the gate reads."""

    score: float
    components: dict = field(default_factory=dict)
    cost_usd: float = 1.0
    n_samples: int = 1


@dataclass
class _ScriptedFitness:
    """Returns ``train_score`` on the train split and ``holdout_score`` on holdout.

    The holdout score is keyed by artifact text so the seed and the candidate
    can differ on the gate: ``holdout_by_text`` overrides ``holdout_score`` for
    a specific candidate render.
    """

    name: str = _FITNESS_NAME
    cost_tier: Literal["free", "paid"] = "paid"
    train_score: float = 0.5
    holdout_score: float = 0.1
    cost_per_eval: float = 1.0
    holdout_by_text: dict[str, float] = field(default_factory=dict)

    async def evaluate(
        self,
        artifact,
        *,
        split: Literal["train", "holdout"],
    ) -> _FakeReport:
        if split == "train":
            return _FakeReport(score=self.train_score, cost_usd=self.cost_per_eval)
        score = self.holdout_by_text.get(artifact.render(), self.holdout_score)
        return _FakeReport(score=score, cost_usd=self.cost_per_eval)


@dataclass
class _SplitRecordingFitness:
    """Records every split the OPTIMIZER (via the bound view) requested.

    The orchestrator wraps this before handing it to the optimizer; the
    wrapper must coerce the split to ``"train"`` so this recorder only ever
    sees ``"train"`` from the optimizer's calls. The gate calls the RAW
    fitness (not the wrapper), so its holdout calls are recorded separately.
    """

    name: str = _FITNESS_NAME
    cost_tier: Literal["free", "paid"] = "paid"
    train_score: float = 0.5
    holdout_score: float = 0.1
    requested_splits_seen_by_optimizer: set[str] = field(default_factory=set)
    _in_optimizer: bool = False

    async def evaluate(self, artifact, *, split):
        if self._in_optimizer:
            self.requested_splits_seen_by_optimizer.add(split)
        score = self.train_score if split == "train" else self.holdout_score
        return _FakeReport(score=score, cost_usd=1.0)


@dataclass
class _EchoOptimizer:
    """A fake optimizer that scores one fixed candidate on the bound (train) view.

    It reaches its fitness through the ``SeedView`` the orchestrator hands it —
    which is the train-bound wrapper — so any split it passes is forced to
    train. Returns the candidate as ``best`` with an empty trial list; the
    orchestrator owns the holdout gate and the diff.
    """

    name: str = "echo"
    candidate: _TextArtifact | None = None
    recorder: _SplitRecordingFitness | None = None

    async def optimize(
        self,
        seed_view: SeedView,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult:
        cand = self.candidate if self.candidate is not None else _TextArtifact(text="candidate")
        fitness = seed_view.fitness_by_name[ladder.rungs[0].fitness_name]
        if self.recorder is not None:
            self.recorder._in_optimizer = True
        # The optimizer *asks* for holdout; the train-bound wrapper coerces it.
        await fitness.evaluate(cand, split="holdout")
        if self.recorder is not None:
            self.recorder._in_optimizer = False
        return OptimizationResult(
            best=cand,
            accepted=False,
            trials=(),
            total_usd=0.0,
            provenance=seed_view.provenance,
        )


# --------------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------------- #
def _ladder() -> FitnessLadder:
    return FitnessLadder(rungs=(Rung(_FITNESS_NAME, max_tasks=6, survivors=1),))


def _provenance() -> Provenance:
    return Provenance(
        seed_fingerprint="s" * 64,
        dataset_revision="test",
        model_ids=("claude-sonnet-5",),
        optimizer="echo",
    )


def _split_recording_fitness() -> _SplitRecordingFitness:
    return _SplitRecordingFitness()


# Each ``_run`` call gets its own ledger file: two calls in one test share a
# ``tmp_path`` but must NOT resume each other's scores (resume by design keys on
# a persisted file), so the counter isolates them.
_LEDGER_COUNTER = 0


async def _run(
    *,
    tmp_path: Path,
    seed_holdout: float = 0.1,
    cand_holdout: float = 0.2,
    fitness: object | None = None,
    cost_per_eval: float = 1.0,
    max_usd: float = 1_000_000.0,
) -> OptimizationResult:
    global _LEDGER_COUNTER
    _LEDGER_COUNTER += 1
    seed = _TextArtifact(text="seed")
    candidate = _TextArtifact(text="candidate")
    recorder = fitness if isinstance(fitness, _SplitRecordingFitness) else None
    if fitness is None:
        fitness = _ScriptedFitness(
            train_score=0.5,
            holdout_score=seed_holdout,
            cost_per_eval=cost_per_eval,
            holdout_by_text={candidate.render(): cand_holdout},
        )
    optimizer = _EchoOptimizer(candidate=candidate, recorder=recorder)
    ledger = TrialsLedger(tmp_path / f"trials-{_LEDGER_COUNTER}.jsonl")
    return await run_optimization(
        seed,
        optimizer,
        _ladder(),
        OptimizationBudget(max_usd=max_usd),
        fitness_by_name={_FITNESS_NAME: fitness},
        ledger=ledger,
        provenance=_provenance(),
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_accepts_only_above_margin(tmp_path) -> None:
    # margin is 0.02: +0.020 exactly → rejected; +0.021 → accepted
    res_eq = await _run(seed_holdout=0.10, cand_holdout=0.12, tmp_path=tmp_path)
    res_gt = await _run(seed_holdout=0.10, cand_holdout=0.121, tmp_path=tmp_path)
    assert res_eq.accepted is False and res_gt.accepted is True


async def test_nonfinite_seed_holdout_aborts_never_autoaccepts(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="seed"):
        await _run(seed_holdout=float("-inf"), cand_holdout=1.0, tmp_path=tmp_path)


async def test_neg_inf_candidate_is_rejected_but_reported(tmp_path) -> None:
    res = await _run(seed_holdout=0.1, cand_holdout=float("-inf"), tmp_path=tmp_path)
    assert res.accepted is False and res.candidate_holdout == float("-inf")


async def test_optimizer_fitness_is_train_bound(tmp_path) -> None:
    recording = _split_recording_fitness()
    await _run(fitness=recording, tmp_path=tmp_path)
    # holdout physically unreachable from the optimizer's bound view
    assert set(recording.requested_splits_seen_by_optimizer) == {"train"}


async def test_budget_exhaustion_stops_and_reports(tmp_path) -> None:
    res = await _run(cost_per_eval=30.0, max_usd=40.0, tmp_path=tmp_path)  # 2nd eval would exceed
    assert res.accepted is False and res.total_usd <= 40.0 and len(res.trials) >= 1


async def test_result_carries_unified_diff_of_proposal(tmp_path) -> None:
    res = await _run(seed_holdout=0.1, cand_holdout=0.2, tmp_path=tmp_path)
    assert res.proposal_diff.startswith("---") and "+++" in res.proposal_diff


def test_accept_margin_is_two_hundredths() -> None:
    assert pytest.approx(0.02) == _ACCEPT_MARGIN
