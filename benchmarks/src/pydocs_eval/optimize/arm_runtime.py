"""Turning an ``arms:`` cell into the objects ONE arm's run needs (design §6).

The run config's ``arms:`` block is DATA — seven validated names and an opaque
settings mapping. This module is the single place that data becomes runnable:

- :func:`resolve_arms` mints each arm's identity (arm hash + objective hash)
  and pairs it with the rubric section its ``scoring.rubric`` names;
- :func:`resolve_arm_dataset` turns ``dataset`` into a live corpus;
- :func:`arm_runner_factory` returns a per-candidate runner factory that
  imports the harness ONLY when a candidate is actually about to be scored;
- :func:`build_arm_fitness` assembles that arm's ``AskRubricFitness``, carrying
  its own objective, its own ``scoring.tracked`` observations, its own
  ``task_name``, and its arm hash for the sample-ledger resume key.

Two laziness rules this module exists to keep:

- **Nothing here runs at config-load time.** ``resolve_arms`` folds the
  delivery-map digest and the objective hash, both of which import the product
  binding; the load-time firewall deliberately checks NAMES only.
- **The harness runtime is imported at SPEND time.** The factory
  :func:`arm_runner_factory` returns is a closure; the ``import langgraph``
  chain happens when a candidate is scored, never when a run is described. A
  zero-spend dry run lists every arm without it.

Budget is NOT per arm, in either of its two shapes. ``max_usd`` and
``max_judge_calls`` are enforced against SHARED objects — one trials ledger
plus one ``BudgetGuard``, one :class:`JudgeCallCounter` — so every arm may be
handed the run-level number and the run still spends the pool once (see
:func:`build_arm_fitness`). ``max_trials`` has no shared enforcer, so it is
DIVIDED across the arms before the passes start
(``dry_run.per_arm_budget``); handing it whole would search N times the
authorized trials.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.ask_binding import build_harness_runner, harness_delivery_map_hash
from pydocs_eval.optimize.fitness.ask_rubric import AskRubricFitness, JudgeCallCounter
from pydocs_eval.optimize.protocols import OptimizableArtifact
from pydocs_eval.optimize.registries import artifact_registry
from pydocs_eval.optimize.rubric.gates import (
    _DEFAULT_MAX_TURNS as _GATE_DEFAULT_MAX_TURNS,
)
from pydocs_eval.optimize.rubric.judge import RubricJudge
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger
from pydocs_eval.optimize.run_config import (
    AskRubricSettings,
    OptimizeRunConfig,
    arm_objective_hash,
    resolve_arm_rubric,
)
from pydocs_eval.registries import dataset_registry

if TYPE_CHECKING:
    from pydocs_mcp.harness.core.run_contract import HarnessRunner

__all__ = [
    "ResolvedArm",
    "arm_runner_factory",
    "arm_runner_settings",
    "build_arm_fitness",
    "resolve_arm_dataset",
    "resolve_arms",
]

# The turn cap assumed when an arm's settings do not spell one. It sizes only
# the TIMEOUT SENTINEL: a timed-out run reports ``turns = cap + 1`` so the
# ``max_turns`` gate fails it. Sourced from the GATE's own default (the
# ``ask_architecture`` precedent) rather than re-typed, because the two are
# coupled by that inequality — a gate threshold raised above a stale sentinel
# would let a timed-out rollout PASS ``max_turns`` and score as if it had
# finished. The eval side cannot import the product's own
# ``AskYourDocsRunnerSettings.max_agent_turns`` default without the ``[ask]``
# extra; the gate constant mirrors it and is base-install safe.
_SENTINEL_MAX_AGENT_TURNS = _GATE_DEFAULT_MAX_TURNS

# Keys an arm's opaque ``settings`` may NOT spell. Each names a value that
# already has an authoritative source WHICH FOLDS INTO AN IDENTITY: the cell's
# own ``tool_names`` key rides the arm hash, and the bound rubric section's
# ``architecture`` rides the objective hash. A second source would let a run
# execute one thing while every row it records names another — and only one of
# the two spellings would separate the arm's ledger rows.
_FORBIDDEN_SETTINGS_KEYS: dict[str, str] = {
    "tool_names": (
        "the bound surface is the cell's own `tool_names` key, which is what arm identity folds"
    ),
    "architecture": (
        "which graph answers is the bound rubric section's `runner.architecture`, "
        "which is what the objective hash folds"
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedArm:
    """One ``arms:`` cell plus everything resolving it produced (design §6).

    ``arm_hash`` is the design §6 formula over this cell; ``objective_hash`` is
    the ONE ``ask_objective_hash`` value the sample ledger keys on and arm
    identity folds. ``rubric_settings`` is the configured section
    ``scoring.rubric`` named — the arm's objective, its pinned architecture,
    its trace root and its per-task timeout all come from there, so two arms
    binding different sections are configured independently end to end.
    """

    label: str
    cell: ArmCell
    arm_hash: str
    objective_hash: str
    rubric_settings: AskRubricSettings


def resolve_arms(cfg: OptimizeRunConfig) -> tuple[ResolvedArm, ...]:
    """Resolve every ``arms:`` cell in ``cfg`` to its identity + objective.

    Empty when the config carries no ``arms:`` block — which is the single
    implicit arm every pre-``arms:`` config runs, and the caller's signal to
    keep exactly today's behavior.

    Raises:
        KeyError: an arm names an unregistered guidance family or runner.
        ValueError: ``scoring.rubric`` names no configured objective.
    """
    return tuple(
        _resolve_one_arm(cfg, arm, label=f"arms[{index}]") for index, arm in enumerate(cfg.arms)
    )


def _resolve_one_arm(cfg: OptimizeRunConfig, arm: ArmCell, *, label: str) -> ResolvedArm:
    """Mint one arm's identity — the three fingerprint inputs, resolved once."""
    objective_hash = arm_objective_hash(cfg, arm, arm_label=label)
    return ResolvedArm(
        label=label,
        cell=arm,
        arm_hash=arm.fingerprint(
            # The arm's own guidance FAMILY seed, not the candidate under
            # test: the candidate's fingerprint is already a separate ledger
            # key component, so folding it here would move the arm hash every
            # trial and destroy its use as a stable, listable arm identity.
            guidance_fingerprint=artifact_registry.build(arm.guidance).fingerprint,
            delivery_map_hash=harness_delivery_map_hash(arm.runner),
            rubric_config_hash=objective_hash,
        ),
        objective_hash=objective_hash,
        rubric_settings=resolve_arm_rubric(cfg, arm, arm_label=label),
    )


def resolve_arm_dataset(arm: ArmCell) -> Dataset:
    """Build the corpus one arm answers over from its ``dataset`` cell key.

    ``dataset`` IS a registered dataset name — the one vocabulary that can
    actually produce a corpus. A second spelling (the task-id prefixes
    ``sweqapro`` / ``ccv``) would have to stay in sync with the registry, with
    ``Dataset.name`` and with the product's ``TASK_NAMES``, and because arm
    identity folds the NAME rather than the resolved corpus, any drift would
    be silent. The 2026-07-28 taxonomy consolidation is the proof: those two
    prefixes now match NO registered dataset and NO enumerated task name, yet
    every prefixed id keeps its exact spelling and split membership, because a
    prefix was only ever a corpus namespace. The load-time firewall checks the
    same registry.

    Raises:
        KeyError: ``dataset`` names no registered dataset (the message carries
            the offending value and the registered names).
    """
    return dataset_registry.build(arm.dataset)


def arm_runner_settings(arm: ResolvedArm) -> dict[str, object]:
    """The harness-private settings mapping this arm constructs its runner from.

    ``cell.settings`` is authoritative and OPAQUE — the harness factory owns
    and validates its keys. The run-level values an arm did not spell are
    filled in from the rubric section this arm binds: ``model`` and
    ``workspace`` because the product settings REQUIRE them (an arm omitting
    either would otherwise fail its first rollout, after earlier arms had
    already spent), and ``trace_root`` because it says where the trace the
    gates read lands. An explicit per-arm value always wins.

    ``tool_names`` and ``architecture`` are the two keys ``settings`` may not
    carry at all — see :data:`_FORBIDDEN_SETTINGS_KEYS`; both are set here from
    their identity-bearing source.

    Raises:
        ValueError: ``settings`` carries ``tool_names`` or ``architecture``.
    """
    settings = dict(arm.cell.settings)
    _reject_identity_bearing_keys(arm, settings)
    runner = arm.rubric_settings.runner
    settings.setdefault("model", runner.model)
    settings.setdefault("workspace", str(runner.workspace))
    settings.setdefault("trace_root", runner.trace_root)
    settings["architecture"] = runner.architecture
    settings["tool_names"] = list(arm.cell.tool_names) if arm.cell.tool_names is not None else None
    return settings


def _reject_identity_bearing_keys(arm: ResolvedArm, settings: dict[str, object]) -> None:
    """Refuse a settings mapping that re-spells a value arm identity folds.

    Raises:
        ValueError: naming the offending key, its value, and where the value
            actually comes from.
    """
    for key, source in _FORBIDDEN_SETTINGS_KEYS.items():
        if key in settings:
            raise ValueError(
                f"{arm.label} settings carries {key} {settings[key]!r}: {source} — "
                "remove it from settings"
            )


def arm_runner_factory(arm: ResolvedArm) -> Callable[[OptimizableArtifact], HarnessRunner]:
    """The per-candidate runner factory for one arm — resolved LAZILY.

    Returns a closure, deliberately: building it imports nothing, so a dry run
    can describe every arm on a machine without the agent runtime. The harness
    module is imported inside :func:`ask_binding.build_harness_runner`, i.e.
    the moment a candidate is about to be scored and not before.

    The candidate itself is NOT a runner setting — it reaches the agent as
    ``guidance_sections`` on every run — so the artifact argument is accepted
    for the ``AskRubricFitness.runner_factory`` shape and intentionally unused.
    """
    settings = arm_runner_settings(arm)
    turns = int(settings.get("max_agent_turns", _SENTINEL_MAX_AGENT_TURNS))

    def factory(artifact: OptimizableArtifact) -> HarnessRunner:
        _ = artifact  # the candidate travels as guidance_sections, not settings
        return build_harness_runner(
            arm.cell.runner,
            settings,
            task_timeout_seconds=arm.rubric_settings.runner.task_timeout_seconds,
            max_agent_turns=turns,
        )

    return factory


def build_arm_fitness(
    arm: ResolvedArm,
    *,
    cfg: OptimizeRunConfig,
    dataset: Dataset,
    runner_factory: Callable[[OptimizableArtifact], HarnessRunner],
    judge: RubricJudge,
    sample_ledger: SampleRubricLedger,
    output_dir: Path,
    judge_calls: JudgeCallCounter,
) -> AskRubricFitness:
    """Assemble ONE arm's fitness from its OWN cell (run-contract design §6).

    Everything that makes this arm's numbers mean something comes from the
    arm: the objective (its resolved rubric + pinned architecture), its
    ``scoring.tracked`` observations, its declared ``task_name``, and the arm
    hash that keys its ledger rows. Everything that bounds the RUN is passed
    in shared: one ``sample_ledger``, one ``judge_calls`` counter, one
    ``max_judge_calls`` ceiling — an arm is a measurement axis, never a second
    budget.
    """
    return AskRubricFitness(
        dataset=dataset,
        runner_factory=runner_factory,
        judge=judge,
        rubric=arm.rubric_settings.rubric_config,
        architecture=arm.rubric_settings.runner.architecture,
        sample_ledger=sample_ledger,
        output_dir=output_dir,
        max_judge_calls=cfg.budget.max_judge_calls,
        rng_seed=cfg.rng_seed,
        tracked_metrics=arm.cell.scoring.tracked,
        arm_hash=arm.arm_hash,
        task_name=arm.cell.task_name,
        judge_calls=judge_calls,
    )
