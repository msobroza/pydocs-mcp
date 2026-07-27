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

The firewall is a KEY firewall since the ``arms:`` block landed (run-contract
design §9 stage 4): ``extra="forbid"`` at the top level, so an unknown or
misspelled section is a load-time error instead of pydantic's default silent
ignore — the same byte-identical-name doctrine applied one level up from
registry names. Widening it is exactly the reviewable event that admits new
arm-level keys.

This module owns the run's typed SHAPE; the name checks themselves live in
``load_firewall`` (which knows nothing about config typing, so it never
imports back). The one half that cannot move is objective RESOLUTION —
``resolve_arm_rubric`` is the single site answering "which objectives does
this config spell" — so the firewall reaches it through a callback.

Every default here refers to the single canonical source it mirrors — the
orchestrator's ``_ACCEPT_MARGIN``, the paired-agent fitness's default weights +
parity floor, and the budget's ``_DEFAULT_*`` constants — so a bump touches one
line, never this file too.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

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
from pydocs_eval.optimize._agent_track_binding import (
    DEFAULT_MODEL,
    DEFAULT_RNG_SEED,
    DEFAULT_TASK_TIMEOUT_SECONDS,
)
from pydocs_eval.optimize._types import OptimizationBudget
from pydocs_eval.optimize.arm_scoring import resolve_rubric_section
from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.ask_binding import (
    _DEFAULT_ASK_ARCHITECTURE,
    DEFAULT_ASK_TRACE_ROOT,
)
from pydocs_eval.optimize.fitness.ask_rubric import ask_objective_hash
from pydocs_eval.optimize.fitness.paired_agent import (
    _DEFAULT_PARITY_FLOOR,
    _DEFAULT_WEIGHTS,
)
from pydocs_eval.optimize.ladder import FitnessLadder
from pydocs_eval.optimize.load_firewall import assert_arm_cells, require_registered
from pydocs_eval.optimize.optimizers.config_search import (
    _DEFAULT_SAMPLE_SIZE,
    _DEFAULT_STRATEGY,
    ConfigSearchOptimizer,
)
from pydocs_eval.optimize.orchestrator import _ACCEPT_MARGIN
from pydocs_eval.optimize.registries import (
    artifact_registry,
    fitness_registry,
    optimizer_registry,
)
from pydocs_eval.optimize.rubric.gates import gate_registry
from pydocs_eval.optimize.rubric.model import (
    _DEFAULT_FAIL_FAST,
    _DEFAULT_GATE_WEIGHT,
    _DEFAULT_RUBRIC_WEIGHT,
    GateCheck,
    RubricConfig,
    RubricCriterion,
    validate_rubric_config,
)

# WHY: the v1 dataset default — the primary agent-track track (spec §D14). The
# YAML restates it for clarity; this constant is the single Python source.
_DEFAULT_DATASET_NAME = "swe-qa-pro"

# WHY: mirrors the harness-ask-your-docs CLI default workspace so a run config that
# omits the key scores against the same index the interactive agent reads.
_DEFAULT_ASK_WORKSPACE = Path("~/pydocs-index")


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


class AskRunnerSettings(BaseModel):
    """How the ask agent + rubric judge are driven on a paid rung (spec §3.5).

    ``model`` serves both the agent and the judge (the same-family reuse the
    agent track defaults to); ``architecture`` pins the ONE graph a prompt
    campaign runs under — ignored when the campaign's artifact is
    ``ask_architecture`` (the candidate carries it then).
    """

    model_config = ConfigDict(frozen=True)

    model: str = DEFAULT_MODEL
    architecture: str = _DEFAULT_ASK_ARCHITECTURE
    # OpenRouter cache-token field-name trap (ADR 0015 §Decision, item 6): this
    # ``base_url`` is the unused reflector-wiring seam (Phase 4). If a future arm
    # points it at OpenRouter's OpenAI-compatible surface, cache activity is named
    # ``prompt_tokens_details.cached_tokens`` / ``cache_write_tokens`` there — NOT
    # the Anthropic-native ``cache_read_input_tokens`` / ``cache_creation_input_tokens``
    # the Phase 2 stream parser (``agent_track/_parse.py:176-177``) keys on. A zero
    # cache reading from an OpenRouter-routed arm is a field-name mismatch, not
    # absent caching — this phase leaves the seam unused so no reading is affected.
    base_url: str | None = None
    workspace: Path = _DEFAULT_ASK_WORKSPACE
    task_timeout_seconds: float = DEFAULT_TASK_TIMEOUT_SECONDS
    # Where the product binding writes each run's ADR 0009 trace. The trace is
    # what the deterministic tool-call gates read (run-contract design §3), so
    # a deployment pointing this at fast local storage is a config change, not
    # a code change.
    trace_root: str = DEFAULT_ASK_TRACE_ROOT


class AskRubricSettings(BaseModel):
    """The configurable gate → rubric → verdict objective (spec §3.4, §3.5).

    Gates and criteria arrive as YAML rows and are coerced to the frozen
    rubric dataclasses; ``rubric_config`` bundles them for the fitness.
    Weight / registry validation runs in ``load_run_config`` so a bad config
    fails at load time, never at trial 14.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    runner: AskRunnerSettings = Field(default_factory=AskRunnerSettings)
    gates: tuple[GateCheck, ...] = ()
    criteria: tuple[RubricCriterion, ...] = ()
    fail_fast: bool = _DEFAULT_FAIL_FAST
    gate_weight: float = _DEFAULT_GATE_WEIGHT
    rubric_weight: float = _DEFAULT_RUBRIC_WEIGHT

    @field_validator("gates", mode="before")
    @classmethod
    def _coerce_gates(cls, value: object) -> object:
        """Build ``GateCheck`` rows from YAML mappings (pass instances through)."""
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                item
                if isinstance(item, GateCheck)
                else GateCheck(
                    name=item["name"], kind=item["kind"], params=dict(item.get("params", {}))
                )
                for item in value
            )
        return value

    @field_validator("criteria", mode="before")
    @classmethod
    def _coerce_criteria(cls, value: object) -> object:
        """Build ``RubricCriterion`` rows from YAML mappings (pass instances through)."""
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(
                item if isinstance(item, RubricCriterion) else RubricCriterion(**item)
                for item in value
            )
        return value

    @property
    def rubric_config(self) -> RubricConfig:
        """The frozen rubric bundle the ask_rubric fitness consumes."""
        return RubricConfig(
            gates=self.gates,
            criteria=self.criteria,
            fail_fast=self.fail_fast,
            gate_weight=self.gate_weight,
            rubric_weight=self.rubric_weight,
        )


class ArchitectureSearchSettings(BaseModel):
    """The ``config_search`` optimizer's knobs (spec §3.5, §4.2).

    ``dimensions`` is the ``enumerate_space`` input — the search grid comes
    from run config, never from code. Defaults mirror the optimizer's own
    canonical constants (imported, never re-encoded).
    """

    model_config = ConfigDict(frozen=True)

    strategy: Literal["grid", "random", "halving"] = _DEFAULT_STRATEGY
    # WHY None: an explicit section seed wins; unset falls back to the run's
    # top-level rng_seed (spec §3.6 — one seed reproduces the draw).
    seed: int | None = None
    sample_size: int = _DEFAULT_SAMPLE_SIZE
    dimensions: Mapping[str, tuple[object, ...]] = Field(default_factory=dict)


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

    ``extra="forbid"``: an unknown top-level key is a config error, never a
    silent no-op (see the module docstring's key-firewall note).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    artifact: str
    optimizer: str
    ladder: FitnessLadder
    fitness: FitnessSettings = Field(default_factory=FitnessSettings)
    accept_margin: float = _ACCEPT_MARGIN
    budget: OptimizationBudget = Field(default_factory=OptimizationBudget)
    llm: CritiqueLlmConfig | None = None
    dataset: DatasetSettings = Field(default_factory=DatasetSettings)
    ask_rubric: AskRubricSettings | None = None
    config_search: ArchitectureSearchSettings | None = None
    # The design §6 experiment arms. Empty by default: every shipped config
    # predates the block and keeps its single implicit arm.
    arms: tuple[ArmCell, ...] = ()
    # WHY: seeds config_search's RNG and task ordering; recorded in
    # provenance so two runs with identical config + ledger are identical
    # modulo LLM nondeterminism (spec §3.6).
    rng_seed: int = DEFAULT_RNG_SEED

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
    require_registered(artifact_registry, cfg.artifact, kind="artifact")
    require_registered(optimizer_registry, cfg.optimizer, kind="optimizer")
    for rung in cfg.ladder.rungs:
        require_registered(fitness_registry, rung.fitness_name, kind="fitness")
    _require_retrieval_rung_compatibility(cfg)
    assert_arm_cells(
        cfg.arms,
        rungs=cfg.ladder.rungs,
        # The config-shaped half of the arm firewall stays HERE: only this
        # module knows which sections a config spells.
        resolve_rubric=lambda arm, label: resolve_arm_rubric(cfg, arm, arm_label=label),
    )
    if cfg.ask_rubric is not None:
        # AC-7/AC-8: gate kinds + rubric weights fail loud at load time.
        validate_rubric_config(
            cfg.ask_rubric.rubric_config, registered_gate_kinds=gate_registry.names()
        )


def _configured_rubric_sections(cfg: OptimizeRunConfig) -> dict[str, AskRubricSettings]:
    """The rubric objectives an arm's ``scoring.rubric`` may name.

    One section today — the top-level ``ask_rubric:`` block, whose key IS its
    name. A second named objective is an additive config section here, which
    is exactly the reviewable event a per-arm objective binding should cost.
    """
    return {"ask_rubric": cfg.ask_rubric} if cfg.ask_rubric is not None else {}


def resolve_arm_rubric(
    cfg: OptimizeRunConfig, arm: ArmCell, *, arm_label: str = "arm"
) -> AskRubricSettings:
    """The rubric SECTION one arm's ``scoring.rubric`` names, by name only.

    The single resolution site: the load-time firewall calls it to prove the
    objective exists, ``arm_objective_hash`` calls it to mint the identity,
    and ``arm_runtime`` calls it to build that arm's fitness — so a rename can
    never leave one of the three resolving something different.

    Raises:
        ValueError: ``scoring.rubric`` names no configured objective.
    """
    return resolve_rubric_section(
        arm.scoring, available=_configured_rubric_sections(cfg), arm_label=arm_label
    )


def arm_objective_hash(cfg: OptimizeRunConfig, arm: ArmCell, *, arm_label: str = "arm") -> str:
    """Resolve one arm's ``scoring.rubric`` to the objective identity it folds.

    The value ``ArmCell.fingerprint(rubric_config_hash=…)`` consumes, and it is
    the SAME value ``AskRubricFitness.objective_hash()`` keys sample-ledger
    lines on — one ``ask_objective_hash`` call, never a second spelling. A
    binding-free variant here would leave arm identity byte-identical across a
    scaffold / gate-source bump that correctly re-runs every sample, and the
    arm would resume rows measured under the old execution path (design §8).

    Raises:
        ValueError: ``scoring.rubric`` names no configured objective.
    """
    settings = resolve_arm_rubric(cfg, arm, arm_label=arm_label)
    return ask_objective_hash(settings.rubric_config, architecture=settings.runner.architecture)


def _require_retrieval_rung_compatibility(cfg: OptimizeRunConfig) -> None:
    """A 'retrieval' rung needs an artifact that carries a retrieval overlay.

    Sweeping a text artifact's render as an AppConfig overlay would measure
    garbage silently — reject the pairing at load time, never at rung 1.
    """
    names_retrieval = any(r.fitness_name == "retrieval" for r in cfg.ladder.rungs)
    if not names_retrieval:
        return
    if not hasattr(artifact_registry.build(cfg.artifact), "retrieval_overlay"):
        raise ValueError(
            f"ladder names the 'retrieval' fitness but artifact {cfg.artifact!r} "
            "carries no retrieval overlay (only retrieval_config / "
            "ask_architecture do) — drop the retrieval rung for text artifacts"
        )


def build_config_search_optimizer(
    cfg: OptimizeRunConfig, *, pipelines_dir: Path | None = None
) -> ConfigSearchOptimizer:
    """Construct the config_search optimizer from the run config (spec §3.5).

    The one place ``ArchitectureSearchSettings`` becomes a live optimizer —
    the CLI (real path and dry-run echo) builds through here so the YAML
    section can never silently drift from what a run executes. An explicit
    section ``seed`` wins; unset falls back to the top-level ``rng_seed``.

    Raises:
        ValueError: the config carries no ``config_search`` section.
    """
    if cfg.config_search is None:
        raise ValueError(
            "run config has no config_search: section — required when optimizer: config_search"
        )
    settings = cfg.config_search
    kwargs: dict[str, object] = {
        "strategy": settings.strategy,
        "seed": settings.seed if settings.seed is not None else cfg.rng_seed,
        "sample_size": settings.sample_size,
        "dimensions": dict(settings.dimensions),
    }
    if pipelines_dir is not None:
        kwargs["pipelines_dir"] = pipelines_dir
    return ConfigSearchOptimizer(**kwargs)  # type: ignore[arg-type]
