"""The ``FitnessLadder`` value object — ordered paired-agent rungs (spec §D3).

A ladder is an ordered tuple of rungs sharing one schema:
``(fitness_name, max_tasks, survivors)``. Candidates enter rung 1; only the
top-``survivors`` by rung score advance. For the v1 text artifacts the ladder
is deliberately degenerate — two sizes of the SAME paid fitness (small-N
screening → larger-N finals). Walking the ladder is the orchestrator's job
(a later slice task); the ladder here stays a pure value object.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rung:
    """One rung: a fitness, a task budget, and a survivor cap (spec §D3).

    ``fitness_name`` is the registry key of the fitness to run;
    ``max_tasks`` bounds how many split tasks the rung scores against;
    ``survivors`` is how many top-scoring candidates advance.
    """

    fitness_name: str
    max_tasks: int
    survivors: int

    def select_survivors(self, scores: Mapping[str, float]) -> tuple[str, ...]:
        """Return the top-``survivors`` candidate keys by descending score.

        Non-finite scores (``-inf`` from a failed judge-parity gate, ``NaN``)
        never survive — they are dropped before ranking. Ties keep the input
        (mapping insertion) order via a stable sort. The result is capped at
        ``survivors``.

        Example:
            >>> Rung("paired_agent", 6, 2).select_survivors({"a": 0.3, "b": 0.1})
            ('a', 'b')
        """
        finite = [key for key, score in scores.items() if math.isfinite(score)]
        # WHY: stable sort keeps insertion order among equal scores, so a tie
        # is deterministic rather than dict-iteration-dependent.
        ranked = sorted(finite, key=lambda key: scores[key], reverse=True)
        return tuple(ranked[: self.survivors])


@dataclass(frozen=True, slots=True)
class FitnessLadder:
    """An ordered tuple of rungs (spec §D3)."""

    rungs: tuple[Rung, ...]

    @classmethod
    def from_lists(cls, raw: Sequence[Sequence[object]]) -> FitnessLadder:
        """Build a ladder from the YAML ``[fitness, max_tasks, survivors]`` rows.

        Each row is the run-config rung schema (spec §D7); a row that is not
        exactly three elements is a config error and raises ``ValueError``
        naming the offending row.

        Example:
            >>> FitnessLadder.from_lists([["paired_agent", 6, 4]]).rungs[0].survivors
            4
        """
        rungs: list[Rung] = []
        for row in raw:
            fields = tuple(row)
            if len(fields) != 3:
                raise ValueError(
                    "rung row must be [fitness, max_tasks, survivors]; "
                    f"got {len(fields)} field(s): {fields!r}"
                )
            fitness_name, max_tasks, survivors = fields
            rungs.append(
                Rung(
                    fitness_name=str(fitness_name),
                    max_tasks=int(max_tasks),  # type: ignore[arg-type]
                    survivors=int(survivors),  # type: ignore[arg-type]
                )
            )
        return cls(rungs=tuple(rungs))
