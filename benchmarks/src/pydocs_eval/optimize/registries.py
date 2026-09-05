"""Decorator registries for the three optimize axes (spec §D2/§D3/§D4).

One instance per pluggable Protocol keeps the namespaces disjoint — an
artifact called ``"tool_docs"`` can't mask a fitness of the same name.
Registered classes are their own factory: ``build(name, **kwargs)`` calls
``cls(**kwargs)``.
"""

from __future__ import annotations

from pydocs_eval.optimize.protocols import (
    FitnessFunction,
    HarnessOptimizer,
    OptimizableArtifact,
)
from pydocs_eval.registries import (
    _Registry,  # WHY: same in-repo registry mechanic as datasets/systems; a second copy would drift
)

# Each populate callback imports its owning impl package for its
# ``@*_registry.register`` decorator side effects, lazily on first read (see
# ``_Registry``). Function-local imports keep the impl→registry cycle broken and
# the registry module light; the optional-dep optimizers (skillopt, gepa) keep
# their library imports function-local, so this populates the NAMES on a base
# install and only errors at USE when the extra is missing.


def _populate_artifacts() -> None:
    from pydocs_eval.optimize import artifacts as _artifacts  # noqa: F401 -- register side effects


def _populate_fitness() -> None:
    from pydocs_eval.optimize import fitness as _fitness  # noqa: F401 -- register side effects


def _populate_optimizers() -> None:
    # The optimizers package registers config_search/critique_refine/skillopt;
    # the gepa optimizer lives in gepa_harness (kept out of that package's
    # __init__ so ``import gepa`` stays lazy), so import its module directly.
    from pydocs_eval.optimize import (
        optimizers as _optimizers,  # noqa: F401 -- register side effects
    )
    from pydocs_eval.optimize.gepa_harness import (
        optimizer as _gepa_optimizer,  # noqa: F401 -- register side effects
    )


artifact_registry: _Registry[OptimizableArtifact] = _Registry(populate=_populate_artifacts)
fitness_registry: _Registry[FitnessFunction] = _Registry(populate=_populate_fitness)
optimizer_registry: _Registry[HarnessOptimizer] = _Registry(populate=_populate_optimizers)
