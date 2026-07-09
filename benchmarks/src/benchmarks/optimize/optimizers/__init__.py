"""Concrete ``HarnessOptimizer`` strategies (spec §D4).

One optimizer per file, each registered under ``optimizer_registry``:
``critique_refine`` (LLM critique-and-rewrite loop) and ``skillopt`` (env-plugin
subprocess adapter). Importing this package registers the optimizers so
``optimizer_registry.build(name, ...)`` resolves them.
"""

from __future__ import annotations

from benchmarks.optimize.optimizers.critique_refine import (
    CritiqueRefineOptimizer,
)
from benchmarks.optimize.optimizers.skillopt import (
    SkillOptOptimizer,
)

__all__ = ["CritiqueRefineOptimizer", "SkillOptOptimizer"]
