"""The ask_rubric paid fitness — gate → rubric → verdict per sample (ACs 9-14, 19)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize._split import task_split
from pydocs_eval.optimize.ask_binding import AskTranscript, FakeAskRunner, ToolCallRecord
from pydocs_eval.optimize.fitness.ask_rubric import AskRubricFitness
from pydocs_eval.optimize.orchestrator import BudgetExhausted
from pydocs_eval.optimize.rubric.judge import FakeRubricJudge
from pydocs_eval.optimize.rubric.model import (
    GateCheck,
    RubricConfig,
    RubricCriterion,
    rubric_config_hash,
)
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger

_QUESTIONS = tuple(f"question {i}?" for i in range(16))
_TRAIN_QUESTIONS = tuple(q for q in _QUESTIONS if task_split(q) == "train")


@dataclass(slots=True)
class _ListDataset:
    name: str = "fake"
    revision: str = "0"

    async def tasks(self):
        for q in _QUESTIONS:
            yield EvalTask(
                task_id=q,
                query=q,
                gold=GoldAnswer(file_set=("pkg/mod.py",)),
                corpus_source=lambda: None,  # type: ignore[arg-type]
                metadata={"qa_type": "how"},
            )


@dataclass(frozen=True, slots=True)
class _Artifact:
    name: str = "ask_prompt"
    content: str = "seed"

    def render(self) -> str:
        return self.content

    def with_content(self, content: str) -> _Artifact:
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()


_CRITERIA = (
    RubricCriterion(name="correctness", weight=0.6, description="right"),
    RubricCriterion(name="grounding", weight=0.4, description="grounded"),
)
_GATES = (GateCheck(name="grounded", kind="gold_substring", params={}),)


def _rubric(**overrides: object) -> RubricConfig:
    fields: dict[str, object] = {
        "gates": _GATES,
        "criteria": _CRITERIA,
        "fail_fast": True,
        "gate_weight": 0.3,
        "rubric_weight": 0.7,
    }
    fields.update(overrides)
    return RubricConfig(**fields)  # type: ignore[arg-type]


def _passing_transcripts() -> dict[str, AskTranscript]:
    return {
        q: AskTranscript(
            answer=f"see pkg/mod.py for {q}",
            tool_calls=(ToolCallRecord("search_codebase", "d"),),
            turns=3,
            cost_usd=0.0,
            wall_seconds=2.0,
        )
        for q in _QUESTIONS
    }


def _scores() -> dict[str, dict[str, float]]:
    return {q: {"correctness": 8.0, "grounding": 6.0} for q in _QUESTIONS}


def _fitness(
    tmp_path: Path,
    *,
    runner: FakeAskRunner | None = None,
    judge: FakeRubricJudge | None = None,
    rubric: RubricConfig | None = None,
    max_judge_calls: int = 200,
) -> tuple[AskRubricFitness, FakeAskRunner, FakeRubricJudge]:
    runner = runner or FakeAskRunner(scripted=_passing_transcripts())
    judge = judge or FakeRubricJudge(scripted=_scores(), cost_per_call=0.1)
    fitness = AskRubricFitness(
        dataset=_ListDataset(),
        runner_factory=lambda artifact: runner,
        judge=judge,
        rubric=rubric or _rubric(),
        architecture="text_react",
        sample_ledger=SampleRubricLedger(tmp_path / "samples.jsonl"),
        output_dir=tmp_path,
        max_judge_calls=max_judge_calls,
    )
    return fitness, runner, judge


def test_paid_tier_and_objective_hash(tmp_path: Path) -> None:
    fitness, _, _ = _fitness(tmp_path)
    assert fitness.cost_tier == "paid"
    assert fitness.objective_hash() == rubric_config_hash(_rubric(), architecture="text_react")


async def test_verdict_is_the_weighted_composite(tmp_path: Path) -> None:
    fitness, _, _ = _fitness(tmp_path)
    report = await fitness.evaluate(_Artifact(), split="train")
    # All gates pass (gpf=1.0); rubric = 0.6*8/10 + 0.4*6/10 = 0.72.
    assert report.score == pytest.approx(0.3 * 1.0 + 0.7 * 0.72)
    assert report.n_samples == len(_TRAIN_QUESTIONS)


async def test_gate_short_circuit_skips_the_judge(tmp_path: Path) -> None:
    # AC-9: fail_fast + a failing gate → verdict 0.0, zero judge calls.
    runner = FakeAskRunner(scripted={})  # empty transcripts fail gold_substring
    fitness, _, judge = _fitness(tmp_path, runner=runner)
    report = await fitness.evaluate(_Artifact(), split="train")
    assert judge.calls == 0
    assert report.score == pytest.approx(0.0)
    assert report.components["judge_skip_rate"] == pytest.approx(1.0)


async def test_full_scoring_calls_the_judge_anyway(tmp_path: Path) -> None:
    # AC-9 parity half: fail_fast=False judges even gate-failing samples.
    runner = FakeAskRunner(scripted={})
    judge = FakeRubricJudge(scripted=_scores())
    fitness, _, _ = _fitness(tmp_path, runner=runner, judge=judge, rubric=_rubric(fail_fast=False))
    await fitness.evaluate(_Artifact(), split="train")
    assert judge.calls == len(_TRAIN_QUESTIONS)


async def test_judge_discard_excludes_the_sample(tmp_path: Path) -> None:
    # AC-10: a malformed judge reply discards, never admits a partial score.
    discard_question = _TRAIN_QUESTIONS[0]
    scores = _scores()
    scores.pop(discard_question)
    judge = FakeRubricJudge(scripted=scores)
    fitness, _, _ = _fitness(tmp_path, judge=judge)
    report = await fitness.evaluate(_Artifact(), split="train")
    assert report.n_samples == len(_TRAIN_QUESTIONS) - 1
    assert report.components["discards"] == pytest.approx(1.0)
    hit = fitness.sample_ledger.lookup(
        fingerprint=_Artifact().fingerprint,
        split="train",
        task_id=discard_question,
        objective_hash=fitness.objective_hash(),
    )
    assert hit is not None and hit.discarded is not None


async def test_sample_resume_skips_runner_and_judge(tmp_path: Path) -> None:
    # AC-11/AC-19: a rerun against the same ledger is entirely free.
    fitness, runner, judge = _fitness(tmp_path)
    await fitness.evaluate(_Artifact(), split="train")
    runner_calls, judge_calls = runner.calls, judge.calls
    report = await fitness.evaluate(_Artifact(), split="train")
    assert runner.calls == runner_calls and judge.calls == judge_calls
    assert report.n_samples == len(_TRAIN_QUESTIONS)


async def test_transcript_file_written_per_sample(tmp_path: Path) -> None:
    fitness, _, _ = _fitness(tmp_path)
    await fitness.evaluate(_Artifact(), split="train")
    sample_dir = tmp_path / "samples" / _Artifact().fingerprint[:12]
    files = sorted(sample_dir.glob("*.json"))
    assert len(files) == len(_TRAIN_QUESTIONS)
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert "answer" in payload and "gates" in payload


async def test_components_carry_the_full_breakdown(tmp_path: Path) -> None:
    # AC-13: per-criterion means, per-gate rates, judge accounting.
    fitness, _, _ = _fitness(tmp_path)
    report = await fitness.evaluate(_Artifact(), split="train")
    for key in (
        "gate_pass_rate",
        "judge_skip_rate",
        "criterion.correctness_mean",
        "criterion.grounding_mean",
        "gate.grounded_rate",
        "judge_calls",
        "discards",
        "turns_mean",
        "wall_seconds_mean",
    ):
        assert key in report.components, key
    assert report.components["criterion.correctness_mean"] == pytest.approx(8.0)
    assert report.components["gate.grounded_rate"] == pytest.approx(1.0)


async def test_zero_admitted_samples_scores_neg_inf(tmp_path: Path) -> None:
    # AC-13: nothing admitted → -inf, dropped by select_survivors, never 0.0.
    judge = FakeRubricJudge(scripted={})  # every reply discards
    fitness, _, _ = _fitness(tmp_path, judge=judge)
    report = await fitness.evaluate(_Artifact(), split="train")
    assert math.isinf(report.score) and report.score < 0
    assert report.n_samples == 0


async def test_max_judge_calls_raises_predictively(tmp_path: Path) -> None:
    # AC-14: call N+1 never starts.
    fitness, _, judge = _fitness(tmp_path, max_judge_calls=2)
    with pytest.raises(BudgetExhausted):
        await fitness.evaluate(_Artifact(), split="train")
    assert judge.calls == 2


async def test_judge_cost_flows_into_the_report(tmp_path: Path) -> None:
    fitness, _, _ = _fitness(tmp_path)
    report = await fitness.evaluate(_Artifact(), split="train")
    assert report.cost_usd == pytest.approx(0.1 * len(_TRAIN_QUESTIONS))


async def test_discarded_samples_are_rejudged_on_resume(tmp_path: Path) -> None:
    # A discard is a judge FAILURE, not a score — a transient timeout must
    # not be resumed forever; the rerun re-pays exactly the discarded ones.
    discard_question = _TRAIN_QUESTIONS[0]
    scores = _scores()
    scores.pop(discard_question)
    judge = FakeRubricJudge(scripted=scores)
    fitness, runner, _ = _fitness(tmp_path, judge=judge)
    await fitness.evaluate(_Artifact(), split="train")
    runner_calls, judge_calls = runner.calls, judge.calls
    report = await fitness.evaluate(_Artifact(), split="train")
    assert runner.calls == runner_calls + 1  # only the discarded sample re-ran
    assert judge.calls == judge_calls + 1
    assert report.n_samples == len(_TRAIN_QUESTIONS) - 1  # still discarded (same scripted miss)


async def test_criterion_mean_keys_survive_all_skipped_rungs(tmp_path: Path) -> None:
    # AC-13: EVERY configured criterion.<name>_mean is present even when the
    # gates skipped the judge for every sample.
    runner = FakeAskRunner(scripted={})  # empty transcripts fail gold_substring
    fitness, _, _ = _fitness(tmp_path, runner=runner)
    report = await fitness.evaluate(_Artifact(), split="train")
    assert report.components["criterion.correctness_mean"] == 0.0
    assert report.components["criterion.grounding_mean"] == 0.0


async def test_end_to_end_rerun_through_the_orchestrator_is_free(tmp_path: Path) -> None:
    # AC-19 end-to-end: two identical run_optimization passes against the
    # same ledgers perform zero fake-runner and zero fake-judge calls on the
    # second run — both resume layers (trials + samples) compose.
    from pydocs_eval.optimize._types import OptimizationBudget, Provenance
    from pydocs_eval.optimize.ladder import FitnessLadder, Rung
    from pydocs_eval.optimize.orchestrator import run_optimization
    from pydocs_eval.optimize.trials_ledger import TrialsLedger

    class _EchoOpt:
        name = "echo"

        async def optimize(self, view, ladder, budget):
            from pydocs_eval.optimize._types import OptimizationResult

            fitness = view.fitness_by_name["ask_rubric"]
            await fitness.evaluate(view.seed, split="train")
            return OptimizationResult(
                best=None, accepted=False, trials=(), total_usd=0.0, provenance=view.provenance
            )

    provenance = Provenance(
        seed_fingerprint="s" * 64, dataset_revision="fake", model_ids=(), optimizer="echo"
    )
    ladder = FitnessLadder(rungs=(Rung("ask_rubric", max_tasks=8, survivors=1),))

    async def _one_pass():
        fitness, runner, judge = _fitness(tmp_path)
        await run_optimization(
            _Artifact(),
            _EchoOpt(),
            ladder,
            OptimizationBudget(),
            fitness_by_name={"ask_rubric": fitness},
            ledger=TrialsLedger(tmp_path / "trials.jsonl"),
            provenance=provenance,
        )
        return runner, judge

    await _one_pass()
    runner, judge = await _one_pass()  # fresh fitness/fakes, same ledgers on disk
    assert runner.calls == 0 and judge.calls == 0
