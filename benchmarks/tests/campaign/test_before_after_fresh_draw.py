"""The fresh repoqa test draw: ``--split-seed`` and ``--exclude-task-ids``.

Filter, then draw: the consumed ids leave the pool before the redraw, so the
draw keeps its full size and is disjoint by construction; a postcondition guard
still refuses, by name, any draw that intersects the excluded set. The pool is
the held-out ``test`` partition at the dataset's OWN split seed — ``--split-seed``
seeds only the redraw, so no ``dev`` task can leak into a "fresh" test sample.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_fresh_draw import (
    ExcludedTaskIds,
    FreshTestDraw,
    draw_fresh_test_tasks,
    fresh_draw_from_args,
    load_excluded_task_ids,
    refuse_overlap_with_excluded_ids,
)
from pydocs_eval.campaign.before_after_split import load_split_tasks, task_ids_of
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

from ._repoqa_fakes import (
    SYNTHETIC_REPOS,
    use_synthetic_repoqa,
    write_exclusion_file,
)

_SMALL_TEST = "repoqa-qa/small_test"


def _task(task_id: str, repo: str) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="q",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=lambda: None,  # type: ignore[arg-type,return-value]
        metadata={"repo": repo},
    )


def _pool(repos: int = 4, per_repo: int = 5) -> list[EvalTask]:
    return [_task(f"r{r}/t{i}", f"repo{r}") for r in range(repos) for i in range(per_repo)]


def _excluded(task_ids: list[str]) -> ExcludedTaskIds:
    return ExcludedTaskIds(
        source="fixture.json", sha256="0" * 64, provenance="test", task_ids=tuple(task_ids)
    )


# --- the exclusion file ------------------------------------------------------


def test_the_exclusion_file_loads_its_ids_provenance_and_digest(tmp_path: Path) -> None:
    path = write_exclusion_file(tmp_path / "ids.json", ["b", "a"], provenance="derived")

    excluded = load_excluded_task_ids(path)

    assert excluded.task_ids == ("a", "b")
    assert excluded.provenance == "derived"
    assert excluded.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert excluded.source == str(path)


@pytest.mark.parametrize(
    ("content", "named"),
    [
        ("not json", "JSON"),
        ('["a"]', "object"),
        ('{"task_ids": ["a"]}', "provenance"),
        ('{"provenance": "", "task_ids": ["a"]}', "provenance"),
        ('{"provenance": "p", "task_ids": []}', "task_ids"),
        ('{"provenance": "p", "task_ids": ["a", 3]}', "3"),
        ('{"provenance": "p", "task_ids": ["a", "a"]}', "'a'"),
    ],
)
def test_a_malformed_exclusion_file_is_refused_naming_what_is_wrong(
    tmp_path: Path, content: str, named: str
) -> None:
    path = tmp_path / "ids.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(MeasurementPlanError) as caught:
        load_excluded_task_ids(path)

    assert named in str(caught.value)
    assert str(path) in str(caught.value)


def test_a_missing_exclusion_file_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(MeasurementPlanError, match="nowhere.json"):
        load_excluded_task_ids(tmp_path / "nowhere.json")


# --- filter, then draw -------------------------------------------------------


def test_the_draw_filters_the_excluded_ids_out_before_drawing_so_it_keeps_its_size() -> None:
    pool = _pool(repos=4, per_repo=5)
    consumed = [f"r{r}/t{i}" for r in range(4) for i in range(2)]

    drawn = draw_fresh_test_tasks(pool, FreshTestDraw(seed=1, excluded=_excluded(consumed)), size=8)

    assert len(drawn) == 8
    assert set(task_ids_of(drawn)).isdisjoint(consumed)
    assert Counter(task.metadata["repo"] for task in drawn) == dict.fromkeys(
        [f"repo{r}" for r in range(4)], 2
    )


def test_the_draw_without_exclusions_is_seeded() -> None:
    pool = _pool(repos=2, per_repo=10)

    first = draw_fresh_test_tasks(pool, FreshTestDraw(seed=1), size=4)
    again = draw_fresh_test_tasks(list(reversed(pool)), FreshTestDraw(seed=1), size=4)
    other = draw_fresh_test_tasks(pool, FreshTestDraw(seed=2), size=4)

    assert task_ids_of(first) == task_ids_of(again)
    assert task_ids_of(first) != task_ids_of(other)


def test_an_excluded_id_outside_the_pool_is_refused_by_name() -> None:
    # A typo, another dataset's id or an unframed record id would exclude nothing.
    draw = FreshTestDraw(seed=1, excluded=_excluded(["r0/t0", "not/a/task"]))

    with pytest.raises(MeasurementPlanError) as caught:
        draw_fresh_test_tasks(_pool(), draw, size=4)

    assert "'not/a/task'" in str(caught.value)
    assert "fixture.json" in str(caught.value)


def test_a_draw_overlapping_the_excluded_set_is_refused_by_name() -> None:
    with pytest.raises(MeasurementPlanError) as caught:
        refuse_overlap_with_excluded_ids(("r0/t0", "r0/t1", "r1/t0"), _excluded(["r1/t0", "zz"]))

    message = str(caught.value)
    assert "'r1/t0'" in message
    assert "'r0/t0'" not in message
    assert "fixture.json" in message


def test_a_draw_disjoint_from_the_excluded_set_passes_the_guard() -> None:
    refuse_overlap_with_excluded_ids(("r0/t0",), _excluded(["r1/t0"]))


# --- through the split loader, over a release-shaped corpus ------------------


async def _consumed_small_test(tmp_path: Path) -> Path:
    """The default ``small_test`` ids, committed the way the real ones are."""
    consumed = task_ids_of(await load_split_tasks(_SMALL_TEST))
    return write_exclusion_file(tmp_path / "consumed.json", list(consumed))


async def _fresh(exclusion: Path, seed: int = 1) -> tuple[str, ...]:
    draw = FreshTestDraw(seed=seed, excluded=load_excluded_task_ids(exclusion))
    return task_ids_of(await load_split_tasks(_SMALL_TEST, draw=draw))


async def test_a_fresh_draw_is_30_tasks_disjoint_from_the_consumed_set_and_from_dev(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_synthetic_repoqa(monkeypatch, tmp_path)
    exclusion = await _consumed_small_test(tmp_path)

    fresh = await _fresh(exclusion)

    consumed = load_excluded_task_ids(exclusion).task_ids
    dev = task_ids_of(await load_split_tasks("repoqa-qa/dev"))
    test = task_ids_of(await load_split_tasks("repoqa-qa/test"))
    assert len(fresh) == 30 == len(set(fresh))
    assert set(fresh).isdisjoint(consumed)
    assert set(fresh).isdisjoint(dev)
    assert set(fresh) <= set(test)


async def test_a_fresh_draw_is_stratified_by_repo_like_small_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_synthetic_repoqa(monkeypatch, tmp_path)
    exclusion = await _consumed_small_test(tmp_path)
    draw = FreshTestDraw(seed=1, excluded=load_excluded_task_ids(exclusion))

    tasks = await load_split_tasks(_SMALL_TEST, draw=draw)

    per_repo = Counter(task.metadata["repo"] for task in tasks)
    assert len(per_repo) == SYNTHETIC_REPOS and set(per_repo.values()) == {3}


async def test_a_fresh_draw_is_stable_across_two_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_synthetic_repoqa(monkeypatch, tmp_path)
    exclusion = await _consumed_small_test(tmp_path)

    assert await _fresh(exclusion) == await _fresh(exclusion)
    assert await _fresh(exclusion, seed=1) != await _fresh(exclusion, seed=2)


async def test_without_a_draw_small_test_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_synthetic_repoqa(monkeypatch, tmp_path)
    exclusion = await _consumed_small_test(tmp_path)

    again = task_ids_of(await load_split_tasks(_SMALL_TEST))

    assert tuple(sorted(again)) == load_excluded_task_ids(exclusion).task_ids


async def test_limit_takes_a_prefix_of_the_fresh_draw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_synthetic_repoqa(monkeypatch, tmp_path)
    draw = FreshTestDraw(seed=1)

    whole = task_ids_of(await load_split_tasks(_SMALL_TEST, draw=draw))
    head = task_ids_of(await load_split_tasks(_SMALL_TEST, limit=2, draw=draw))

    assert head == whole[:2]


@pytest.mark.parametrize(
    ("spec", "named"),
    [
        ("repoqa-qa/dev", "'dev'"),
        ("repoqa-qa/test", "'test'"),
        ("swe-qa-questions/small_test", "'swe-qa-questions'"),
        ("repo_qa/small_test", "'swe-qa-questions'"),
    ],
)
async def test_a_fresh_draw_of_anything_but_repoqa_qa_small_test_is_refused_by_name(
    spec: str, named: str
) -> None:
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks(spec, draw=FreshTestDraw(seed=1))

    assert named in str(caught.value)
    assert "repoqa-qa/small_test" in str(caught.value)


# --- the CLI flags and the record --------------------------------------------


def _args(**overrides: object) -> argparse.Namespace:
    return argparse.Namespace(**{"split_seed": None, "exclude_task_ids": None, **overrides})


def test_no_flag_means_no_draw() -> None:
    assert fresh_draw_from_args(_args()) is None


def test_split_seed_alone_draws_without_exclusions() -> None:
    assert fresh_draw_from_args(_args(split_seed=1)) == FreshTestDraw(seed=1)


def test_both_flags_load_the_exclusion_file(tmp_path: Path) -> None:
    path = write_exclusion_file(tmp_path / "ids.json", ["a"])

    draw = fresh_draw_from_args(_args(split_seed=1, exclude_task_ids=path))

    assert draw == FreshTestDraw(seed=1, excluded=load_excluded_task_ids(path))


def test_exclusions_without_a_seed_are_refused(tmp_path: Path) -> None:
    path = write_exclusion_file(tmp_path / "ids.json", ["a"])

    with pytest.raises(MeasurementPlanError, match="--split-seed"):
        fresh_draw_from_args(_args(exclude_task_ids=path))


def test_the_draw_round_trips_through_its_json_record() -> None:
    draw = FreshTestDraw(seed=1, excluded=_excluded(["a", "b"]))

    assert FreshTestDraw.from_record(json.loads(json.dumps(asdict(draw)))) == draw
    bare = FreshTestDraw(seed=3)
    assert FreshTestDraw.from_record(json.loads(json.dumps(asdict(bare)))) == bare


def test_two_draws_are_one_sample_whatever_path_named_the_file() -> None:
    here = FreshTestDraw(seed=1, excluded=_excluded(["a"]))
    there = FreshTestDraw(seed=1, excluded=replace(_excluded(["a"]), source="elsewhere.json"))

    assert here.sample_key() == there.sample_key()
    assert here.sample_key() != FreshTestDraw(seed=2, excluded=_excluded(["a"])).sample_key()
    assert here.sample_key() != FreshTestDraw(seed=1).sample_key()


def test_a_task_without_a_repo_is_refused_rather_than_drawn_unstratified() -> None:
    pool = [*_pool(repos=1, per_repo=3), _task("bare/t0", "")]

    with pytest.raises(MeasurementPlanError, match="'bare/t0'"):
        draw_fresh_test_tasks(pool, FreshTestDraw(seed=1), size=2)


async def test_the_wrong_slice_of_repoqa_qa_is_refused_naming_the_expected_one() -> None:
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("repoqa-qa/test", draw=FreshTestDraw(seed=1))

    assert "expected repoqa-qa/small_test" in str(caught.value)
    assert "[]" not in str(caught.value)


def test_the_plan_lines_name_the_seed_the_digest_the_count_and_the_provenance() -> None:
    lines = "\n".join(FreshTestDraw(seed=1, excluded=_excluded(["a", "b"])).plan_lines())

    assert "seed 1" in lines
    assert "fixture.json" in lines and "0" * 64 in lines
    assert "2 id(s)" in lines
    assert "provenance: test" in lines
