"""The three pluggable seams of the optimize layer (spec §D2, §D3, §D4).

One Protocol per axis: WHAT gets optimized (``OptimizableArtifact``), HOW a
candidate is scored (``FitnessFunction``), and WHICH strategy proposes
candidates (``HarnessOptimizer``). All ``@runtime_checkable`` so the
registries can assert a registered class satisfies its axis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from benchmarks.optimize._types import (
    FitnessReport,
    OptimizationBudget,
    OptimizationResult,
)

if TYPE_CHECKING:
    # WHY: ``FitnessLadder`` lands in ``optimize/ladder.py`` (a later slice
    # task) and is only named in the annotation below; under
    # ``from __future__ import annotations`` the reference stays a string, so
    # a TYPE_CHECKING-only import keeps this module importable before the
    # ladder exists and avoids a forward dependency at runtime.
    from benchmarks.optimize.ladder import FitnessLadder


@runtime_checkable
class OptimizableArtifact(Protocol):
    """A text artifact a run improves (spec §D2)."""

    name: str

    def render(self) -> str: ...
    def with_content(self, content: str) -> OptimizableArtifact: ...
    def validate(self) -> tuple[str, ...]: ...  # constraint violations; () == valid
    def landing_note(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...  # sha256 of render()


@runtime_checkable
class FitnessFunction(Protocol):
    """Scores a candidate on a train or holdout split (spec §D3)."""

    name: str
    cost_tier: Literal["free", "paid"]

    async def evaluate(
        self,
        artifact: OptimizableArtifact,
        *,
        split: Literal["train", "holdout"],
    ) -> FitnessReport: ...


@runtime_checkable
class HarnessOptimizer(Protocol):
    """Proposes candidates by walking a ladder within a budget (spec §D4)."""

    name: str

    async def optimize(
        self,
        seed: OptimizableArtifact,
        ladder: FitnessLadder,
        budget: OptimizationBudget,
    ) -> OptimizationResult: ...
