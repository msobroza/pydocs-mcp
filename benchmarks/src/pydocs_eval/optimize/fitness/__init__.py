"""Fitness functions for the optimize layer (spec §D3). Each fitness lives in
its own module; importing this package fires the ``@fitness_registry.register``
decorators so callers can look them up by name.

``paired_agent`` is the one paid fitness of the v1 ladder — it scores a text
artifact by running the slice-5 paired agent-track twice (seed vs candidate)
with a judge-parity pre-gate. ``retrieval`` is free-tier SCAFFOLDING for a
future structured-artifact slice, wired into no v1 ladder.
"""

from __future__ import annotations

from .ask_rubric import AskRubricFitness
from .paired_agent import ArtifactInjection, PairedAgentFitness
from .retrieval import RetrievalFitness

__all__ = [
    "ArtifactInjection",
    "AskRubricFitness",
    "PairedAgentFitness",
    "RetrievalFitness",
]
