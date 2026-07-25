"""Run plans for a multi-dataset pool — the ACROSS-runs axis.

Orthogonal to sampling: a plan decides how many training runs happen and how
they chain, while a sampler decides what one run's batches look like. They
compose, so an experiment names both (``single × stratified``,
``curriculum × uniform``, …).

No SkillOpt here: a plan drives an injected ``train`` callable, so every arm is
exercised offline for free — the same discipline ``skillopt.py`` keeps with its
monkeypatchable ``_invoke_train``.
"""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.multitask.plans import (
    TrainRequest,
    TrainResult,
    build_plan,
    plan_registry,
)

_SHIPPED = ("curriculum", "per_dataset", "single")


def _rows() -> tuple[dict[str, object], ...]:
    return tuple(
        {"task_id": f"{t}/{i}", "task_type": t, "question": "q", "gold": "g"}
        for t, n in (("ccv", 2), ("sweqapro", 3))
        for i in range(n)
    )


class _FakeTrainer:
    """Records every request and returns a skill naming what it was trained on."""

    def __init__(self) -> None:
        self.requests: list[TrainRequest] = []

    def __call__(self, request: TrainRequest) -> TrainResult:
        self.requests.append(request)
        return TrainResult(
            skill=f"{request.seed_skill}+{request.label}",
            label=request.label,
            scores={str(row["task_id"]): 1.0 for row in request.rows},
        )


def test_registry_ships_exactly_the_three_arms() -> None:
    assert tuple(sorted(plan_registry.names())) == _SHIPPED


# --------------------------------------------------------------------------- #
# single — the control
# --------------------------------------------------------------------------- #


def test_single_runs_once_over_the_merged_pool() -> None:
    trainer = _FakeTrainer()
    outcome = build_plan("single").execute("SEED", _rows(), trainer)

    assert len(trainer.requests) == 1
    assert len(trainer.requests[0].rows) == 5
    assert set(trainer.requests[0].task_types) == {"ccv", "sweqapro"}
    assert outcome.skill == "SEED+all"


# --------------------------------------------------------------------------- #
# per_dataset — one run per type, then merge
# --------------------------------------------------------------------------- #


def test_per_dataset_runs_once_per_type_each_seeing_only_its_own_rows() -> None:
    trainer = _FakeTrainer()
    build_plan("per_dataset").execute("SEED", _rows(), trainer)

    assert [r.label for r in trainer.requests] == ["ccv", "sweqapro"]  # sorted, deterministic
    for request in trainer.requests:
        assert {row["task_type"] for row in request.rows} == {request.label}


def test_per_dataset_branches_every_run_from_the_SAME_seed() -> None:
    """Independence is the point — otherwise it is a curriculum, not a fan-out."""
    trainer = _FakeTrainer()
    build_plan("per_dataset").execute("SEED", _rows(), trainer)
    assert [r.seed_skill for r in trainer.requests] == ["SEED", "SEED"]


def test_per_dataset_merges_every_run_into_one_skill() -> None:
    outcome = build_plan("per_dataset").execute("SEED", _rows(), _FakeTrainer())
    assert len(outcome.runs) == 2
    for run in outcome.runs:
        assert run.skill in outcome.skill  # concatenation keeps both, verbatim


# --------------------------------------------------------------------------- #
# curriculum — sequential, each run seeded with the previous best
# --------------------------------------------------------------------------- #


def test_curriculum_chains_each_run_from_the_previous_best() -> None:
    trainer = _FakeTrainer()
    outcome = build_plan("curriculum").execute("SEED", _rows(), trainer)

    assert [r.seed_skill for r in trainer.requests] == ["SEED", "SEED+ccv"]
    assert outcome.skill == "SEED+ccv+sweqapro"


def test_curriculum_order_is_configurable_and_changes_the_result() -> None:
    """Order-dependence is inherent to this arm, so it must be explicit."""
    trainer = _FakeTrainer()
    build_plan("curriculum", order=("sweqapro", "ccv")).execute("SEED", _rows(), trainer)
    assert [r.label for r in trainer.requests] == ["sweqapro", "ccv"]


def test_curriculum_order_omitting_a_present_type_is_rejected() -> None:
    """`order` fixes the SEQUENCE, not the membership.

    An order of ("ccv",) over a ccv+sweqapro pool trained on a strict subset while
    still reporting a curriculum result — the sweqapro rows were silently never
    trained on.
    """
    with pytest.raises(ValueError, match="sweqapro"):
        build_plan("curriculum", order=("ccv",)).execute("SEED", _rows(), _FakeTrainer())


def test_curriculum_rejects_an_order_naming_an_absent_type() -> None:
    with pytest.raises(ValueError, match="nope"):
        build_plan("curriculum", order=("nope",)).execute("SEED", _rows(), _FakeTrainer())


# --------------------------------------------------------------------------- #
# Shared contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_plan_records_each_run_and_a_final_skill(name: str) -> None:
    outcome = build_plan(name).execute("SEED", _rows(), _FakeTrainer())
    assert outcome.runs
    assert outcome.skill
    assert outcome.plan == name


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_plan_reports_per_type_scores(name: str) -> None:
    """The comparison payload: a blended mean hides a minority regression."""
    outcome = build_plan(name).execute("SEED", _rows(), _FakeTrainer())
    assert set(outcome.score_by_type) == {"ccv", "sweqapro"}


@pytest.mark.parametrize("name", _SHIPPED)
def test_every_plan_rejects_an_empty_pool(name: str) -> None:
    with pytest.raises(ValueError, match="empty"):
        build_plan(name).execute("SEED", (), _FakeTrainer())
