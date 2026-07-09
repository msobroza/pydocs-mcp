"""Value-object + registry contract for the optimize layer (plan Task 2)."""

from __future__ import annotations

import math

import pytest

from benchmarks.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
    Provenance,
    Trial,
)
from benchmarks.optimize.registries import (
    artifact_registry,
    fitness_registry,
    optimizer_registry,
)


def test_fitness_report_fields() -> None:
    r = FitnessReport(score=0.195, components={"tokens_fraction": 0.2}, cost_usd=1.5, n_samples=6)
    assert r.score == pytest.approx(0.195) and r.n_samples == 6


def test_budget_defaults_are_conservative() -> None:
    bud = OptimizationBudget()
    assert bud.max_trials == 20 and bud.max_usd == pytest.approx(40.0)
    assert bud.wall_timeout_seconds == 14400.0


def test_trial_and_result_shapes() -> None:
    t = Trial(fingerprint="f" * 64, rung_scores=(0.1,), cost_usd=0.5, violations=())
    res = OptimizationResult(
        best=None,
        accepted=False,
        trials=(t,),
        total_usd=0.5,
        provenance=Provenance(
            seed_fingerprint="s" * 64,
            dataset_revision="r",
            model_ids=("claude-sonnet-5",),
            optimizer="critique_refine",
        ),
    )
    assert res.accepted is False and res.trials[0].cost_usd == pytest.approx(0.5)
    assert math.isfinite(t.rung_scores[0])


def test_registries_are_distinct_and_register() -> None:
    assert artifact_registry is not fitness_registry is not optimizer_registry

    @artifact_registry.register("_probe")
    class _Probe:  # throwaway registry probe
        pass

    assert artifact_registry.build("_probe").__class__ is _Probe
