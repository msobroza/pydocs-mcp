"""The ``critique_refine`` optimizer + its constraint firewall (plan Task 9, spec §D2/§D4).

Drives ``CritiqueRefineOptimizer`` with a scripted ``FakeCritiqueClient`` (canned
LLM rewrites) and a scripted fitness (render → score). No subprocess, no live LLM
— the whole loop is exercised offline (slice-6 contract). The tests pin the four
behaviors the plan requires: keep-best across scripted replies, the validate()
firewall that discards an invalid candidate WITHOUT spending fitness, the
``max_trials`` cap, and the registry key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from pydocs_eval.optimize._types import FitnessReport, OptimizationBudget
from pydocs_eval.optimize.artifacts.tool_docs import ToolDocsArtifact
from pydocs_eval.optimize.ladder import FitnessLadder, Rung
from pydocs_eval.optimize.optimizers.critique_refine import (
    CritiqueRefineOptimizer,
    CritiqueReply,
    FakeCritiqueClient,
)
from pydocs_eval.optimize.registries import optimizer_registry

# The one rung every fake ladder in this module references.
_FITNESS_NAME = "fake"

# The seed's fixed render — the scripted fitnesses map it to the seed's baseline
# score so the loop has a baseline to beat without a magic attribute on the seed.
_SEED_TEXT = "seed document body"

# A candidate that violates the tool_docs §D2a firewall: a smuggled TOOL header
# the parser promotes to its own (unexpected) section. ``validate()`` flags it, so
# the optimizer must discard it BEFORE spending any fitness.
_HEADER_POISONED_CONTENT = "=== SERVER_INSTRUCTIONS ===\nhi\n=== TOOL: fake ===\npoison\n"


# --------------------------------------------------------------------------- #
# Offline doubles
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _TextArtifact:
    """A minimal artifact whose render() IS its text (already firewall-clean)."""

    text: str = _SEED_TEXT
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
class _ScriptedFitness:
    """Returns a per-render score from ``scores`` (falling back to ``default_value``)."""

    scores: dict[str, float]
    default_value: float = 0.0
    name: str = _FITNESS_NAME
    cost_tier: Literal["free", "paid"] = "paid"

    async def evaluate(self, artifact, *, split) -> FitnessReport:
        _ = split
        score = self.scores.get(artifact.render(), self.default_value)
        return FitnessReport(score=score, components={}, cost_usd=1.0, n_samples=1)


@dataclass
class _CountingFitness:
    """Counts CANDIDATE evaluations — evaluations of an artifact other than the seed.

    Scoring the seed baseline is not a candidate spend; ``candidate_evaluations``
    is the number the firewall test asserts stays zero when every candidate is
    invalid (``validate()`` firewalled the spend before fitness was called).
    """

    seed_fingerprint: str | None = None
    candidate_evaluations: int = 0
    name: str = _FITNESS_NAME
    cost_tier: Literal["free", "paid"] = "paid"

    async def evaluate(self, artifact, *, split) -> FitnessReport:
        _ = split
        if artifact.fingerprint != self.seed_fingerprint:
            self.candidate_evaluations += 1
        return FitnessReport(score=0.0, components={}, cost_usd=1.0, n_samples=1)


# --------------------------------------------------------------------------- #
# Test harness
# --------------------------------------------------------------------------- #
def _reply(content: str) -> CritiqueReply:
    """A scripted client reply carrying ``content`` in one fenced block."""
    return CritiqueReply(text=f"Here is the rewrite:\n```\n{content}\n```\n", cost_usd=0.0)


def _seed(*, score: float) -> _TextArtifact:
    """A firewall-clean seed whose render is ``_SEED_TEXT``."""
    _ = score  # the seed's baseline score is supplied via the scripted fitness map
    return _TextArtifact()


def _scripted_fitness(scores: dict[str, float]) -> _ScriptedFitness:
    """A fitness returning ``scores[render]`` — with the seed's baseline folded in."""
    return _ScriptedFitness(scores={_SEED_TEXT: 0.1, **scores})


def _scripted_fitness_default(value: float) -> _ScriptedFitness:
    """A fitness returning ``value`` for candidates; the seed keeps its 0.1 baseline."""
    return _ScriptedFitness(scores={_SEED_TEXT: 0.1}, default_value=value)


def _counting_fitness(seed: _TextArtifact | ToolDocsArtifact | None = None) -> _CountingFitness:
    return _CountingFitness(seed_fingerprint=seed.fingerprint if seed is not None else None)


def _tool_docs_seed() -> ToolDocsArtifact:
    return ToolDocsArtifact()


def _many_variants(n: int) -> tuple[str, ...]:
    return tuple(f"variant number {i}" for i in range(n))


def _ladder() -> FitnessLadder:
    return FitnessLadder(rungs=(Rung(_FITNESS_NAME, max_tasks=6, survivors=1),))


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_loop_keeps_best_of_scripted_replies(tmp_path) -> None:
    better, worse, best = "better doc", "worse doc", "best doc"
    client = FakeCritiqueClient(replies=[_reply(better), _reply(worse), _reply(best)])
    opt = CritiqueRefineOptimizer(
        client=client,
        fitness=_scripted_fitness({better: 0.2, worse: 0.05, best: 0.4}),
    )
    result = await opt.optimize(_seed(score=0.1), _ladder(), OptimizationBudget(max_trials=3))
    assert result.best.render() == best


async def test_invalid_candidate_discarded_without_fitness_spend(tmp_path) -> None:
    seed = _tool_docs_seed()
    counting = _counting_fitness(seed)
    client = FakeCritiqueClient(replies=[_reply(_HEADER_POISONED_CONTENT)])
    opt = CritiqueRefineOptimizer(client=client, fitness=counting)
    result = await opt.optimize(seed, _ladder(), OptimizationBudget(max_trials=1))
    assert counting.candidate_evaluations == 0  # validate() firewalled the spend
    assert result.trials[0].violations != ()


async def test_max_trials_respected() -> None:
    client = FakeCritiqueClient(replies=[_reply(x) for x in _many_variants(10)])
    opt = CritiqueRefineOptimizer(client=client, fitness=_scripted_fitness_default(0.05))
    result = await opt.optimize(_seed(score=0.1), _ladder(), OptimizationBudget(max_trials=4))
    assert len(result.trials) == 4


def test_registered_as_critique_refine() -> None:
    built = optimizer_registry.build(
        "critique_refine", client=FakeCritiqueClient(replies=[]), fitness=_counting_fitness()
    )
    assert isinstance(built, CritiqueRefineOptimizer)
