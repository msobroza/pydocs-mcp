"""Concrete ``HarnessOptimizer`` strategies (spec §D4).

One optimizer per file, each registered under ``optimizer_registry``:
``config_search`` (grid/random/halving over enumerable cells),
``critique_refine`` (LLM critique-and-rewrite loop) and ``skillopt`` (env-plugin
subprocess adapter). Importing this package registers the optimizers so
``optimizer_registry.build(name, ...)`` resolves them. (The ``gepa`` optimizer
lives in the sibling ``gepa_harness`` package — deliberately outside its
``__init__`` so ``import gepa`` stays lazy — and is registered by the
``optimizer_registry`` populate callback importing that module directly.)
"""

from __future__ import annotations

from pydocs_eval.optimize.optimizers.config_search import (
    ConfigSearchOptimizer,
)
from pydocs_eval.optimize.optimizers.critique_refine import (
    CritiqueRefineOptimizer,
)
from pydocs_eval.optimize.optimizers.skillopt import (
    SkillOptOptimizer,
)

__all__ = ["ConfigSearchOptimizer", "CritiqueRefineOptimizer", "SkillOptOptimizer"]
