"""The config_search optimizer — grid | random | halving over enumerable cells (AC-16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from pydocs_eval.optimize._types import FitnessReport, OptimizationBudget
from pydocs_eval.optimize.artifacts.ask_architecture import AskArchitectureArtifact
from pydocs_eval.optimize.ladder import FitnessLadder, Rung
from pydocs_eval.optimize.optimizers.config_search import ConfigSearchOptimizer
from pydocs_eval.optimize.registries import optimizer_registry


@pytest.fixture
def pipelines_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "pipelines"
    directory.mkdir()
    (directory / "exp_a.yaml").write_text("search: {}\n", encoding="utf-8")
    (directory / "exp_b.yaml").write_text("search: {}\n", encoding="utf-8")
    return directory


_DIMS = {
    "architecture": ("text_react",),
    "rewrite_enabled": (True, False),
    "scope_pin": (True,),
    "retrieval_config": ("exp_a", "exp_b"),
    "max_agent_turns": (8, 12),
}
_CELLS = 1 * 2 * 1 * 2 * 2  # 8


@dataclass(slots=True)
class _CountingFitness:
    """Scores by turn count (higher turns win) and records every candidate."""

    name: str
    cost_tier: Literal["free", "paid"] = "free"
    seen: list[str] = field(default_factory=list)

    async def evaluate(self, artifact, *, split) -> FitnessReport:
        self.seen.append(artifact.fingerprint)
        import yaml

        parsed = yaml.safe_load(artifact.render())
        score = float(parsed["max_agent_turns"]) + (1.0 if parsed["rewrite_enabled"] else 0.0)
        return FitnessReport(score=score, components={}, cost_usd=0.0, n_samples=1)


def _seed(pipelines_dir: Path) -> AskArchitectureArtifact:
    return AskArchitectureArtifact(retrieval_config="exp_a", pipelines_dir=pipelines_dir)


def _ladder(
    rung1: str = "retrieval", rung2: str = "ask_rubric", survivors: int = 2
) -> FitnessLadder:
    return FitnessLadder(
        rungs=(Rung(rung1, max_tasks=6, survivors=survivors), Rung(rung2, max_tasks=6, survivors=1))
    )


def _optimizer(pipelines_dir: Path, **overrides: object) -> tuple[ConfigSearchOptimizer, dict]:
    fitnesses = {
        "retrieval": _CountingFitness(name="retrieval"),
        "ask_rubric": _CountingFitness(name="ask_rubric"),
    }
    fields_: dict[str, object] = {
        "strategy": "halving",
        "seed": 0,
        "dimensions": _DIMS,
        "pipelines_dir": pipelines_dir,
        "fitness_by_name": fitnesses,
    }
    fields_.update(overrides)
    return ConfigSearchOptimizer(**fields_), fitnesses  # type: ignore[arg-type]


def test_registered() -> None:
    assert "config_search" in optimizer_registry.names()


async def test_grid_visits_all_cells(pipelines_dir: Path) -> None:
    optimizer, fitnesses = _optimizer(pipelines_dir, strategy="grid")
    result = await optimizer.optimize(_seed(pipelines_dir), _ladder(), OptimizationBudget())
    # Grid scores every cell end-to-end on the FINAL rung (no screening).
    assert len(set(fitnesses["ask_rubric"].seen)) == _CELLS
    assert result.accepted is False


async def test_random_is_seeded(pipelines_dir: Path) -> None:
    first, f1 = _optimizer(pipelines_dir, strategy="random", seed=7, sample_size=4)
    again, f2 = _optimizer(pipelines_dir, strategy="random", seed=7, sample_size=4)
    other, f3 = _optimizer(pipelines_dir, strategy="random", seed=8, sample_size=4)
    budget = OptimizationBudget()
    await first.optimize(_seed(pipelines_dir), _ladder(), budget)
    await again.optimize(_seed(pipelines_dir), _ladder(), budget)
    await other.optimize(_seed(pipelines_dir), _ladder(), budget)
    assert f1["ask_rubric"].seen == f2["ask_rubric"].seen  # equal seeds → identical draw
    assert f1["ask_rubric"].seen != f3["ask_rubric"].seen  # different seeds differ


async def test_halving_screens_on_rung_one(pipelines_dir: Path) -> None:
    # AC-16: all cells on rung 1; only rung-1 survivors reach rung 2.
    optimizer, fitnesses = _optimizer(pipelines_dir, strategy="halving")
    await optimizer.optimize(_seed(pipelines_dir), _ladder(survivors=2), OptimizationBudget())
    assert len(set(fitnesses["retrieval"].seen)) == _CELLS
    assert len(set(fitnesses["ask_rubric"].seen)) == 2


async def test_best_is_the_final_rung_argmax(pipelines_dir: Path) -> None:
    optimizer, _ = _optimizer(pipelines_dir, strategy="grid")
    result = await optimizer.optimize(_seed(pipelines_dir), _ladder(), OptimizationBudget())
    assert result.best is not None
    import yaml

    winner = yaml.safe_load(result.best.render())
    assert winner["max_agent_turns"] == 12 and winner["rewrite_enabled"] is True


async def test_invalid_cells_are_never_scored(pipelines_dir: Path) -> None:
    dims = {**_DIMS, "retrieval_config": ("exp_a", "exp_missing")}
    optimizer, fitnesses = _optimizer(pipelines_dir, strategy="grid", dimensions=dims)
    result = await optimizer.optimize(_seed(pipelines_dir), _ladder(), OptimizationBudget())
    assert len(set(fitnesses["ask_rubric"].seen)) == _CELLS // 2  # exp_missing cells firewalled
    assert any(t.violations for t in result.trials)


async def test_trials_carry_every_scored_cell(pipelines_dir: Path) -> None:
    optimizer, _ = _optimizer(pipelines_dir, strategy="grid")
    result = await optimizer.optimize(_seed(pipelines_dir), _ladder(), OptimizationBudget())
    assert len(result.trials) == _CELLS
