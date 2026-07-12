"""Retrieval fitness — candidate-overlay injection + split subsetting (AC-6).

Offline: ``run_sweep`` and the dataset-id collector are monkeypatched; the
tests prove the candidate's render reaches the sweep as the sole overlay and
the split's pinned task subset reaches it as the whitelist. No sweep, no
dataset build, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pydocs_eval.optimize._split import partition_task_ids
from pydocs_eval.optimize._types import FitnessReport
from pydocs_eval.optimize.fitness import retrieval as retrieval_mod
from pydocs_eval.optimize.fitness.retrieval import RetrievalFitness

# Enough ids that the pinned sha256 predicate lands some on each side.
_TASK_IDS = tuple(f"task-{i}" for i in range(16))


@dataclass(frozen=True, slots=True)
class _OverlayArtifact:
    """A retrieval_config stand-in whose render IS the overlay YAML."""

    name: str = "retrieval_config"
    content: str = "search:\n  default_limit: 7\n"

    def render(self) -> str:
        return self.content

    def with_content(self, content: str) -> _OverlayArtifact:
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()

    def retrieval_overlay(self) -> str:
        return self.render()


@pytest.fixture
def sweep_capture(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def _fake_run_sweep(**kwargs):
        captured.update(kwargs)
        return {("pydocs", "cfg"): {"recall@5": (0.75, 0.70, 0.80)}}, 3

    async def _fake_task_ids(dataset_name, dataset_kwargs):
        captured["ids_dataset"] = dataset_name
        return _TASK_IDS

    monkeypatch.setattr(retrieval_mod, "run_sweep", _fake_run_sweep)
    monkeypatch.setattr(retrieval_mod, "_dataset_task_ids", _fake_task_ids)
    return captured


def _fitness(tmp_path: Path) -> RetrievalFitness:
    return RetrievalFitness(metric_specs=("recall@5",), output_dir=tmp_path)


def test_retrieval_fitness_is_free_tier() -> None:
    assert RetrievalFitness().cost_tier == "free"


async def test_candidate_render_is_the_sole_overlay(sweep_capture, tmp_path) -> None:
    artifact = _OverlayArtifact()
    report = await _fitness(tmp_path).evaluate(artifact, split="train")
    assert isinstance(report, FitnessReport)
    assert report.score == pytest.approx(0.75)
    (overlay,) = sweep_capture["config_paths"]  # exactly one entry
    assert Path(overlay).read_text(encoding="utf-8") == artifact.render()
    assert artifact.fingerprint[:12] in Path(overlay).stem


async def test_split_subsets_tasks_via_the_pinned_predicate(sweep_capture, tmp_path) -> None:
    train, holdout = partition_task_ids(_TASK_IDS)
    await _fitness(tmp_path).evaluate(_OverlayArtifact(), split="train")
    assert sweep_capture["task_ids"] == frozenset(train)
    await _fitness(tmp_path).evaluate(_OverlayArtifact(), split="holdout")
    assert sweep_capture["task_ids"] == frozenset(holdout)


async def test_distinct_candidates_get_distinct_overlays(sweep_capture, tmp_path) -> None:
    fit = _fitness(tmp_path)
    await fit.evaluate(_OverlayArtifact(content="search: {}\n"), split="train")
    first = sweep_capture["config_paths"]
    await fit.evaluate(_OverlayArtifact(content="output: {}\n"), split="train")
    assert sweep_capture["config_paths"] != first


async def test_artifacts_without_an_overlay_are_rejected_loudly(sweep_capture, tmp_path) -> None:
    # A text artifact's render is NOT an AppConfig overlay — sweeping it
    # would measure garbage silently; the fitness must refuse.
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _TextArtifact:
        name: str = "ask_prompt"

        def render(self) -> str:
            return "=== SYSTEM_PROMPT ===\nnot an overlay\n"

        @property
        def fingerprint(self) -> str:
            return "f" * 64

    with pytest.raises(TypeError, match="retrieval overlay"):
        await _fitness(tmp_path).evaluate(_TextArtifact(), split="train")
