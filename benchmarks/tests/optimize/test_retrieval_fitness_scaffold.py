"""Retrieval fitness is scaffolding wired into NO v1 ladder (plan Task 6 / spec §D3).

One offline unit test drives ``RetrievalFitness.evaluate`` with a synthetic
in-memory artifact and a monkeypatched ``run_sweep`` to prove the seam — the
fitness wraps ``run_sweep`` behind the shared ``evaluate`` shape and returns a
``FitnessReport``. No sweep, no dataset build, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.fitness import retrieval as retrieval_mod
from pydocs_eval.optimize.fitness.retrieval import RetrievalFitness


@dataclass(frozen=True, slots=True)
class _SyntheticArtifact:
    """A structured-artifact stand-in (future-slice shape; unused fields)."""

    name: str = "retrieval_config"
    content: str = "{}"

    def render(self) -> str:
        return self.content

    def with_content(self, content: str) -> _SyntheticArtifact:
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()


def test_retrieval_fitness_is_free_tier() -> None:
    assert RetrievalFitness().cost_tier == "free"


async def test_evaluate_wraps_run_sweep(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    async def _fake_run_sweep(**kwargs):
        captured.update(kwargs)
        results = {("pydocs", "cfg"): {"recall@5": (0.75, 0.70, 0.80)}}
        return results, 3

    monkeypatch.setattr(retrieval_mod, "run_sweep", _fake_run_sweep)
    fit = RetrievalFitness(
        systems=("pydocs",),
        config_paths=(tmp_path / "cfg.yaml",),
        dataset_name="repoqa",
        metric_specs=("recall@5",),
    )
    report = await fit.evaluate(_SyntheticArtifact(), split="train")
    assert isinstance(report, FitnessReport)
    assert report.n_samples == 3
    assert report.score == pytest.approx(0.75)  # the primary-metric mean
    assert captured["systems"] == ("pydocs",)
