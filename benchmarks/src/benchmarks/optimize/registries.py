"""Decorator registries for the three optimize axes (spec §D2/§D3/§D4).

One instance per pluggable Protocol keeps the namespaces disjoint — an
artifact called ``"tool_docs"`` can't mask a fitness of the same name.
Registered classes are their own factory: ``build(name, **kwargs)`` calls
``cls(**kwargs)``.
"""

from __future__ import annotations

from benchmarks.eval.serialization import (
    _Registry,  # WHY: same in-repo registry mechanic as datasets/systems; a second copy would drift
)
from benchmarks.optimize.protocols import (
    FitnessFunction,
    HarnessOptimizer,
    OptimizableArtifact,
)

artifact_registry: _Registry[OptimizableArtifact] = _Registry()
fitness_registry: _Registry[FitnessFunction] = _Registry()
optimizer_registry: _Registry[HarnessOptimizer] = _Registry()
