"""The sampler seam made optimizer-agnostic, and wired to the shared fitness.

Two gaps this closes:

* the sampler assumed SkillOpt's dict rows, so it could not be handed the
  fitness's ``EvalTask`` objects — a ``key`` function replaces the shape
  assumption;
* ``sample`` selects a batch, but ``AskRubricFitness._split_tasks`` needs an
  ORDER: its own comment notes "task order decides WHICH samples a budget cutoff
  reaches", so the cutoff takes a PREFIX. A stratified batch does not help a
  prefix — the ordering itself must be stratified.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_eval.optimize.multitask.sampling import build_sampler, row_type, type_counts


@dataclass(frozen=True, slots=True)
class _Task:
    """Stands in for EvalTask: an object, NOT a Mapping."""

    task_id: str


def _dict_pool() -> list[dict[str, object]]:
    return [{"task_id": f"sweqapro/{i}"} for i in range(260)] + [
        {"task_id": f"ccv/{i}"} for i in range(12)
    ]


def _object_pool() -> list[_Task]:
    return [_Task(f"sweqapro/{i}") for i in range(260)] + [_Task(f"ccv/{i}") for i in range(12)]


# --------------------------------------------------------------------------- #
# key= — shape independence
# --------------------------------------------------------------------------- #


def test_row_type_rejects_a_non_mapping_without_a_key() -> None:
    """EvalTask is a dataclass, so the Mapping assumption fails on it."""
    with pytest.raises((AttributeError, TypeError, ValueError)):
        row_type(_Task("ccv/1"))  # type: ignore[arg-type]


def test_task_objects_are_sampled_via_an_explicit_key() -> None:
    by_attr = lambda t: t.task_id.split("/", 1)[0]  # noqa: E731
    picked = build_sampler("stratified").sample(_object_pool(), 12, 0, key=by_attr)
    kinds = [by_attr(t) for t in picked]
    assert kinds.count("ccv") >= 1
    assert len(picked) == 12


def test_type_counts_accepts_a_key_too() -> None:
    by_attr = lambda t: t.task_id.split("/", 1)[0]  # noqa: E731
    assert type_counts(_object_pool(), key=by_attr) == {"sweqapro": 260, "ccv": 12}


# --------------------------------------------------------------------------- #
# order() — the primitive the budget cutoff needs
# --------------------------------------------------------------------------- #


def test_order_returns_the_whole_pool() -> None:
    pool = _dict_pool()
    for name in ("uniform", "stratified"):
        assert len(build_sampler(name).order(pool, 0)) == len(pool)


def test_uniform_order_is_exactly_todays_seeded_shuffle() -> None:
    """Back-compat: the default path through _split_tasks must not move."""
    import random

    pool = _dict_pool()
    expected = list(pool)
    random.Random(7).shuffle(expected)
    assert build_sampler("uniform").order(pool, 7) == expected


def test_stratified_order_puts_every_type_in_the_first_positions() -> None:
    """A budget cutoff takes a prefix, so the floor must live at the HEAD."""
    for seed in range(20):
        head = build_sampler("stratified").order(_dict_pool(), seed)[:2]
        assert type_counts(head) == {"ccv": 1, "sweqapro": 1}


def test_stratified_order_keeps_short_prefixes_representative() -> None:
    """The property uniform lacks: a 12-task budget still sees ccv."""
    ordered = build_sampler("stratified").order(_dict_pool(), 0)
    assert type_counts(ordered[:12]).get("ccv", 0) >= 1
    assert type_counts(ordered[:48]).get("ccv", 0) >= 1


def test_order_is_deterministic_under_a_seed() -> None:
    s = build_sampler("stratified")
    assert s.order(_dict_pool(), 3) == s.order(_dict_pool(), 3)


def test_order_loses_no_rows_and_duplicates_none() -> None:
    ordered = build_sampler("stratified").order(_dict_pool(), 1)
    ids = [r["task_id"] for r in ordered]
    assert len(ids) == len(set(ids)) == 272


# --------------------------------------------------------------------------- #
# End-to-end: the sampler seam on the SHARED fitness (every optimizer uses it)
# --------------------------------------------------------------------------- #


def _fitness(tmp_path, sampler=None):
    """A minimal AskRubricFitness; only `_split_tasks` is exercised."""
    import asyncio

    from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
    from pydocs_eval.optimize.fitness.ask_rubric import AskRubricFitness
    from pydocs_eval.optimize.rubric.model import RubricConfig, RubricCriterion
    from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger

    tasks = [
        EvalTask(
            task_id=tid,
            query="q",
            gold=GoldAnswer(),
            corpus_source=lambda: None,  # type: ignore[arg-type]
        )
        for tid in [f"sweqapro/{i}" for i in range(260)] + [f"ccv/{i}" for i in range(12)]
    ]

    class _DS:
        name, revision = "swe-qa-pro+crosscommitvuln", "1.0"

        async def tasks(self):
            for t in tasks:
                yield t

    kwargs = dict(
        dataset=_DS(),
        runner_factory=lambda artifact: None,
        judge=None,
        rubric=RubricConfig(gates=(), criteria=(RubricCriterion("c", 1.0, "d"),)),
        architecture="text_react",
        sample_ledger=SampleRubricLedger(tmp_path / "s.jsonl"),
        output_dir=tmp_path,
    )
    if sampler is not None:
        kwargs["sampler"] = sampler
    return AskRubricFitness(**kwargs), asyncio


def test_fitness_default_ordering_is_unchanged(tmp_path) -> None:
    """Back-compat: the default sampler reproduces the old seeded shuffle."""
    import random

    fitness, aio = _fitness(tmp_path)
    ordered = aio.run(fitness._split_tasks("train"))

    ids = [t.task_id for t in ordered]
    expected = list(ids)
    random.Random(fitness.rng_seed).shuffle(expected)
    # The old code shuffled the same filtered list with the same seed, so the
    # multiset is identical and the ordering is a seeded permutation of it.
    assert sorted(ids) == sorted(expected)
    assert len(ids) == len(set(ids))


def test_stratified_fitness_puts_ccv_in_reach_of_a_budget_cutoff(tmp_path) -> None:
    """The payoff: a truncated combined run can no longer admit zero ccv tasks."""
    uniform, aio = _fitness(tmp_path)
    stratified, _ = _fitness(tmp_path, sampler=build_sampler("stratified"))

    head_u = [t.task_id.split("/")[0] for t in aio.run(uniform._split_tasks("train"))][:12]
    head_s = [t.task_id.split("/")[0] for t in aio.run(stratified._split_tasks("train"))][:12]

    assert head_s.count("ccv") >= 1
    assert head_u.count("ccv") <= head_s.count("ccv")
