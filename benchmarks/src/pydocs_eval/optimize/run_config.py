"""The typed optimize run config loaded from YAML (spec §D7).

``OptimizeRunConfig`` is a benchmarks-local pydantic model — NOT product
``AppConfig`` — mirroring the spec's canonical YAML: which artifact, which
optimizer, the ``FitnessLadder`` (rungs as ``[fitness, max_tasks, survivors]``),
the fitness weights + judge-parity floor, the holdout ``accept_margin``, the
budget, the (``critique_refine``-only) LLM config, and the dataset selector.

``load_run_config(path)`` parses the YAML and — the §D7 contract — validates
every registry key (``artifact`` / ``optimizer`` / each rung's ``fitness_name``)
against the three optimize registries at load time. Byte-identical names are the
rule: a typo like ``gradient_descent`` is a ``KeyError`` naming the bad key and
the registered names, never a silent no-op.

Every default here refers to the single canonical source it mirrors — the
orchestrator's ``_ACCEPT_MARGIN``, the paired-agent fitness's default weights +
parity floor, and the budget's ``_DEFAULT_*`` constants — so a bump touches one
line, never this file too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# WHY: importing these three packages fires the @*_registry.register decorators
# on every concrete artifact / fitness / optimizer, so ``names()`` is populated
# before ``_assert_registry_keys`` consults it — importing the registries module
# alone yields empty registries. ``artifacts`` / ``fitness`` are re-referenced
# below (so ruff sees them used); ``optimizers`` is a pure side-effect import.
import pydocs_eval.optimize.artifacts
import pydocs_eval.optimize.fitness
import pydocs_eval.optimize.optimizers  # noqa: F401  (registration side effect only)
from pydocs_eval.optimize._types import OptimizationBudget
from pydocs_eval.optimize.fitness.paired_agent import (
    _DEFAULT_PARITY_FLOOR,
    _DEFAULT_WEIGHTS,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.orchestrator import _ACCEPT_MARGIN
from pydocs_eval.optimize.registries import (
    artifact_registry,
    fitness_registry,
    optimizer_registry,
)

# WHY: the v1 dataset default — the primary agent-track track (spec §D14). The
# YAML restates it for clarity; this constant is the single Python source.
_DEFAULT_DATASET_NAME = "swe-qa-pro"


class FitnessSettings(BaseModel):
    """Fitness knobs from the run config (spec §D3/§D7).

    ``judge_parity_floor`` and ``weights`` default to the paired-agent fitness's
    own canonical constants (imported, never re-encoded) so the run config and
    the fitness never disagree.
    """

    model_config = ConfigDict(frozen=True)

    judge_parity_floor: float = _DEFAULT_PARITY_FLOOR
    weights: Mapping[str, float] = Field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))


class CritiqueLlmConfig(BaseModel):
    """The ``critique_refine`` LLM client config (spec §D4/§D7).

    Benchmarks-local (mirrors the product's LLM config shape but never reads
    product ``AppConfig``): provider / model / temperature come from the run
    config so an A/B of critique models is a YAML edit. Present only for
    ``critique_refine`` runs; ``skillopt`` configs omit the ``llm`` block.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    model_name: str
    temperature: float = 0.7


class DatasetSettings(BaseModel):
    """Which dataset (and optional fixture) the run scores against (spec §D7).

    ``fixture_path`` lets a dry run walk split determinism over a committed
    fixture JSONL with no network; unset, the dataset resolves its release.
    """

    model_config = ConfigDict(frozen=True)

    name: str = _DEFAULT_DATASET_NAME
    fixture_path: Path | None = None


class OptimizeRunConfig(BaseModel):
    """The whole optimize run, typed (spec §D7).

    The ladder is coerced from the YAML ``[[fitness, max_tasks, survivors], ...]``
    rows via ``FitnessLadder.from_lists``; the budget from the ``{max_trials,
    max_usd, wall_timeout_seconds}`` block. ``arbitrary_types_allowed`` lets the
    two frozen dataclasses (``FitnessLadder`` / ``OptimizationBudget``) live as
    fields without a pydantic mirror of each.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    artifact: str
    optimizer: str
    ladder: FitnessLadder
    fitness: FitnessSettings = Field(default_factory=FitnessSettings)
    accept_margin: float = _ACCEPT_MARGIN
    budget: OptimizationBudget = Field(default_factory=OptimizationBudget)
    llm: CritiqueLlmConfig | None = None
    dataset: DatasetSettings = Field(default_factory=DatasetSettings)

    @field_validator("ladder", mode="before")
    @classmethod
    def _coerce_ladder(cls, value: object) -> object:
        """Build a ``FitnessLadder`` from the YAML rung rows (pass instances through)."""
        if isinstance(value, FitnessLadder):
            return value
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return FitnessLadder.from_lists(value)
        raise TypeError(
            "ladder must be a list of [fitness, max_tasks, survivors] rows; "
            f"got {type(value).__name__}"
        )

    @field_validator("budget", mode="before")
    @classmethod
    def _coerce_budget(cls, value: object) -> object:
        """Build an ``OptimizationBudget`` from the YAML mapping (pass instances through)."""
        if isinstance(value, OptimizationBudget):
            return value
        if isinstance(value, Mapping):
            return OptimizationBudget(**dict(value))
        raise TypeError(
            "budget must be a mapping of {max_trials, max_usd, wall_timeout_seconds}; "
            f"got {type(value).__name__}"
        )


def load_run_config(path: Path) -> OptimizeRunConfig:
    """Parse ``path`` into a typed ``OptimizeRunConfig`` and validate registry keys.

    The §D7 firewall: after typed parsing, ``artifact`` must be a registered
    artifact, ``optimizer`` a registered optimizer, and every rung's
    ``fitness_name`` a registered fitness — byte-identical to the registered
    names. A miss raises ``KeyError`` naming the offending key and the registered
    names (from ``_Registry.build``), so a typo fails loud at load time rather
    than surfacing mid-run.

    Raises:
        KeyError: an ``artifact`` / ``optimizer`` / rung fitness name is not
            registered.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg = OptimizeRunConfig.model_validate(raw)
    _assert_registry_keys(cfg)
    return cfg


def _assert_registry_keys(cfg: OptimizeRunConfig) -> None:
    """Raise ``KeyError`` if any registry key in ``cfg`` is not registered (§D7).

    Uses each registry's own ``build``-style lookup miss message (offending key +
    the sorted registered names) so the error is actionable without re-deriving
    the registry contents here.
    """
    _require_registered(artifact_registry, cfg.artifact, kind="artifact")
    _require_registered(optimizer_registry, cfg.optimizer, kind="optimizer")
    for rung in cfg.ladder.rungs:
        _require_registered(fitness_registry, rung.fitness_name, kind="fitness")


def _require_registered(registry: object, name: str, *, kind: str) -> None:
    """Assert ``name`` is registered in ``registry``, else raise a naming ``KeyError``."""
    if name not in registry.names():  # type: ignore[attr-defined]
        raise KeyError(
            f"unknown {kind} {name!r}; have {list(registry.names())}"  # type: ignore[attr-defined]
        )
