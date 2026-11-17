"""After a fresh-draw run: the verbs that re-read its arms use the draw the arms RECORDED.

``--report-only`` and ``before-after-compare`` score stored answers against a
split's gold. For a fresh-draw run that gold belongs to the redraw, and the
arms' own ``arm_settings.json`` froze it (seed and excluded ids) at plan time —
so that record is what they load through, whether or not the operator repeats
the flags. Flags that name a different sample, or arms that answered different
samples, are refused by name; a re-edited exclusion file cannot move them.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import read_arm_settings, write_arm_settings
from pydocs_eval.campaign.before_after_fresh_draw import FreshTestDraw, load_excluded_task_ids

from ._fakes import FakeArmRun, FakeSplitTasks, before_after_argv, git_repo_with_two_descriptions
from ._repoqa_fakes import write_exclusion_file
from .test_before_after_compare import _rows, _write_arm
from .test_before_after_fresh_draw_command import _arm_settings

_SMALL_TEST = "repoqa-qa/small_test"


def _draw(tmp_path: Path, seed: int = 1) -> FreshTestDraw:
    exclusion = write_exclusion_file(tmp_path / "consumed.json", ["c1", "c2"], provenance="p")
    return FreshTestDraw(seed=seed, excluded=load_excluded_task_ids(exclusion))


def _record_draw(arm_dir: Path, draw: FreshTestDraw | None) -> Path:
    """Rewrite ``arm_dir``'s settings as an arm that answered ``draw``."""
    arm_dir.mkdir(parents=True, exist_ok=True)
    if not (arm_dir / "arm_settings.json").exists():
        write_arm_settings(arm_dir, _arm_settings(arm_dir))
    settings = read_arm_settings(arm_dir)
    write_arm_settings(arm_dir, dataclasses.replace(settings, task_draw=draw))
    return arm_dir


# --- before-after-compare ----------------------------------------------------


def _compare(baseline: Path, variant: Path, *extra: str) -> int:
    argv = ["before-after-compare", "--baseline", str(baseline), "--aa-replicate", str(baseline)]
    return main([*argv, "--variant", str(variant), "--split", _SMALL_TEST, *extra])


def test_compare_scores_against_the_draw_its_arms_recorded_and_prints_it(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    draw = _draw(tmp_path)
    baseline = _record_draw(_write_arm(tmp_path, "baseline", _rows()), draw)
    variant = _record_draw(_write_arm(tmp_path, "variant", _rows()), draw)

    assert _compare(baseline, variant) != 2  # a verdict, not an input error

    assert compare_split.draws == [draw]
    assert "draw:       fresh small_test, seed 1" in capsys.readouterr().out


def test_compare_refuses_arms_that_answered_different_draws(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _record_draw(_write_arm(tmp_path, "baseline", _rows()), _draw(tmp_path, seed=1))
    variant = _record_draw(_write_arm(tmp_path, "variant", _rows()), _draw(tmp_path, seed=2))

    assert _compare(baseline, variant) == 2

    err = capsys.readouterr().err
    assert str(baseline) in err and str(variant) in err
    assert compare_split.draws == []


def test_compare_without_a_recorded_draw_loads_the_slice_as_is(
    tmp_path: Path, compare_split: FakeSplitTasks
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())

    assert _compare(baseline, _write_arm(tmp_path, "variant", _rows())) != 2

    assert compare_split.draws == [None]


# --- before-after --report-only ----------------------------------------------


def _recorded_run(tmp_path: Path, draw: FreshTestDraw | None) -> None:
    for role in ("baseline", "candidate"):
        _record_draw(tmp_path / "out" / role, draw)


def _report_only(tmp_path: Path, repo: Path, *extra: str) -> int:
    argv = before_after_argv(tmp_path, repo, "--report-only", *extra)
    return main([*argv, "--split", _SMALL_TEST])


def test_report_only_rescores_the_recorded_draw_when_the_flags_are_left_out(
    tmp_path: Path, stub_command: FakeArmRun, before_after_split: FakeSplitTasks
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    draw = _draw(tmp_path)
    _recorded_run(tmp_path, draw)

    _report_only(tmp_path, repo)  # no arm.json: refused after the split is loaded

    assert before_after_split.draws == [draw]


def test_report_only_refuses_flags_naming_another_sample(
    tmp_path: Path,
    stub_command: FakeArmRun,
    before_after_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    _recorded_run(tmp_path, _draw(tmp_path, seed=1))
    exclusion = str(tmp_path / "consumed.json")

    assert _report_only(tmp_path, repo, "--split-seed", "2", "--exclude-task-ids", exclusion) == 2

    assert "seed 1" in capsys.readouterr().err
    assert before_after_split.draws == []


def test_report_only_refuses_an_exclusion_file_edited_since_the_arms_ran(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    _recorded_run(tmp_path, _draw(tmp_path, seed=1))
    edited = write_exclusion_file(tmp_path / "consumed.json", ["c1", "c2", "c3"])

    assert _report_only(tmp_path, repo, "--split-seed", "1", "--exclude-task-ids", str(edited)) == 2

    assert "sha256" in capsys.readouterr().err


def test_report_only_accepts_the_same_file_under_another_path(
    tmp_path: Path, stub_command: FakeArmRun, before_after_split: FakeSplitTasks
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    draw = _draw(tmp_path)
    _recorded_run(tmp_path, draw)
    elsewhere = shutil.copy(tmp_path / "consumed.json", tmp_path / "copy.json")

    _report_only(tmp_path, repo, "--split-seed", "1", "--exclude-task-ids", str(elsewhere))

    assert before_after_split.draws == [draw]


def test_report_only_refuses_a_draw_over_arms_that_answered_none(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    _recorded_run(tmp_path, None)

    assert _report_only(tmp_path, repo, "--split-seed", "1") == 2

    assert "--split-seed" in capsys.readouterr().err


# --- the arm child -----------------------------------------------------------


def test_an_arm_child_whose_draw_is_refused_exits_2_naming_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _record_draw(tmp_path, _draw(tmp_path))

    async def refused(split: str, **kwargs: object) -> tuple[()]:
        raise MeasurementPlanError("excluded id outside the pool")

    monkeypatch.setattr(before_after_command, "assert_product_under", lambda path: None)
    monkeypatch.setattr(before_after_command, "load_split_tasks", refused)
    argv = ["before-after-arm", "--settings", str(tmp_path / "arm_settings.json")]

    code = main([*argv, "--split", _SMALL_TEST, "--expect-product-under", str(tmp_path)])

    assert code == 2
    assert "excluded id outside the pool" in capsys.readouterr().err
