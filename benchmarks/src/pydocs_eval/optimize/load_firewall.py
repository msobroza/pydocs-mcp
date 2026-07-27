"""The load-time NAME firewall a run config passes before anything runs (§D7).

One doctrine, spelled once: every name a config spells must be byte-identical
to a registered one, and a mismatch is a ``KeyError`` / ``ValueError`` naming
the offending value and the values that would have worked — raised while
loading, never first at trial 14.

Lives beside ``run_config`` rather than inside it so the config module stays
the typed SHAPE of a run while this module stays its VALIDITY, and so the arm
firewall can grow (it is the reviewable event that admits new arm-level keys)
without pushing the config module past the file-size cap.

Nothing here imports a harness or an agent runtime: the checks are name-level
by contract, because a harness behind an optional extra must cost nothing
until an arm actually runs it (run-contract design §6).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydocs_eval.optimize.arm_scoring import objective_fitness_name
from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.ask_binding import harness_bridge_for, known_task_names
from pydocs_eval.optimize.ladder import Rung
from pydocs_eval.optimize.registries import artifact_registry
from pydocs_eval.registries import dataset_registry

__all__ = ["assert_arm_cells", "require_registered"]


def require_registered(registry: object, name: str, *, kind: str) -> None:
    """Assert ``name`` is registered in ``registry``, else raise a naming ``KeyError``."""
    if name not in registry.names():  # type: ignore[attr-defined]
        raise KeyError(
            f"unknown {kind} {name!r}; have {list(registry.names())}"  # type: ignore[attr-defined]
        )


def assert_arm_cells(
    arms: Sequence[ArmCell],
    *,
    rungs: Sequence[Rung],
    resolve_rubric: Callable[[ArmCell, str], object],
) -> None:
    """Validate every ``arms:`` cell's NAME keys at load time (design §6).

    Six checks: ``guidance`` must be a registered artifact family, ``runner`` a
    registered harness-bridge row, ``dataset`` a registered dataset (the corpus
    an arm actually resolves through ``arm_runtime.resolve_arm_dataset``),
    ``task_name`` one of the product loader's enumerated v1 task names,
    ``scoring.rubric`` a rubric objective the config actually spells (proved by
    ``resolve_rubric``, the caller's single resolution site), and the ladder's
    FINAL rung the fitness that computes ``scoring.objective``. Cell SHAPE
    (unknown keys, a ``tool_names`` that escapes the frozen nine, an unknown
    objective or tracked metric) already failed in ``ArmCell`` / ``ArmScoring``.

    Raises:
        KeyError: an arm names an unregistered guidance family, runner or
            dataset.
        ValueError: an arm names a task outside the enumerated v1 set, a rubric
            objective the config does not configure, or an objective the
            ladder's final rung does not rank on.
    """
    task_names = known_task_names() if arms else ()
    for index, arm in enumerate(arms):
        label = f"arms[{index}]"
        _require_ladder_ranks_the_objective(arm, rungs=rungs, arm_label=label)
        require_registered(artifact_registry, arm.guidance, kind="arm guidance artifact")
        harness_bridge_for(arm.runner)
        # The dataset registry is the ONE vocabulary that can actually produce
        # a corpus, and it is import-safe at load time (eval-local only, no
        # product / harness import) — so the name an arm spells is checked in
        # the same load-time firewall as every other cell key.
        require_registered(dataset_registry, arm.dataset, kind="arm dataset")
        _require_enumerated_task_name(arm, task_names=task_names, arm_label=label)
        # Resolving proves the objective EXISTS — an unspellable rubric name
        # fails here, never first at trial 14. Deliberately NOT the hash: the
        # identity VALUE folds ``ask_binding_identity()``, which imports the
        # harness binding, and load-time laziness is pinned by contract
        # (design §6 — a harness behind an optional extra costs nothing until
        # an arm runs). ``arm_objective_hash`` mints it at fingerprint time,
        # where the delivery-map input already pays that same import.
        resolve_rubric(arm, label)


def _require_enumerated_task_name(
    arm: ArmCell, *, task_names: Sequence[str], arm_label: str
) -> None:
    """The arm's framing must be one the product loader enumerates.

    Raises:
        ValueError: ``task_name`` is outside the fixed v1 set.
    """
    if arm.task_name in task_names:
        return
    raise ValueError(
        f"{arm_label} names task_name {arm.task_name!r}; the v1 task names are the fixed "
        f"set {list(task_names)} — widening them is a product event "
        "(skill_artifact_loader.TASK_NAMES), not a config edit"
    )


def _require_ladder_ranks_the_objective(
    arm: ArmCell, *, rungs: Sequence[Rung], arm_label: str
) -> None:
    """The ladder's FINAL rung must rank on the fitness this arm's objective needs.

    An arm's fitness is installed under the name its objective binds; the
    acceptance gate scores through ``rungs[-1].fitness_name``. When the two
    disagree the arm's fitness is built and then never called: the config
    loads, the dry run walks green, and zero samples are scored against the
    objective the arm declared. Checked HERE so that is a load-time error and
    never a silently unmeasured paid run.

    Raises:
        ValueError: the final rung names a different fitness.
    """
    required = objective_fitness_name(arm.scoring.objective)
    final = rungs[-1].fitness_name
    if final == required:
        return
    raise ValueError(
        f"{arm_label} scores objective {str(arm.scoring.objective)!r}, which the "
        f"{required!r} fitness computes, but the ladder's final rung ranks on {final!r} "
        f"(rungs: {[r.fitness_name for r in rungs]}) — the arm's objective would never "
        "be measured"
    )
