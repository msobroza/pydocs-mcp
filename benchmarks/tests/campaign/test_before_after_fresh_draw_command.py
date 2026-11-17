"""``--split-seed`` / ``--exclude-task-ids`` through ``before-after``, its arms and its report.

What an operator depends on: the plan prints the seed, the exclusion file's
sha256, its id count and its provenance; each arm's settings carry the draw
(with the ids frozen at plan time) and the arm child loads the SAME tasks from
them; the report's provenance names the draw; and a run that names neither
flag loads, plans and records exactly what it did before the flags existed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import ArmRole
from pydocs_eval.campaign.before_after_arm import (
    ArmSettings,
    ArmSummary,
    read_arm_settings,
    write_arm_settings,
)
from pydocs_eval.campaign.before_after_arm_launch import arm_settings_for
from pydocs_eval.campaign.before_after_fresh_draw import (
    ExcludedTaskIds,
    FreshTestDraw,
    load_excluded_task_ids,
)
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_report import render_report

from ._fakes import FakeArmRun, FakeSplitTasks, before_after_argv, git_repo_with_two_descriptions
from ._repoqa_fakes import write_exclusion_file
from .test_before_after import _BASELINE, _plan


def _exclusion(tmp_path: Path) -> Path:
    return write_exclusion_file(tmp_path / "consumed.json", ["c1", "c2"], provenance="derived")


def _draw_argv(tmp_path: Path, repo: Path, *extra: str) -> list[str]:
    exclusion = str(_exclusion(tmp_path))
    return before_after_argv(
        tmp_path, repo, "--split-seed", "1", "--exclude-task-ids", exclusion, *extra
    )


def test_the_plan_prints_the_seed_and_the_exclusion_files_digest_count_and_provenance(
    tmp_path: Path,
    stub_command: FakeArmRun,
    before_after_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(_draw_argv(tmp_path, repo)) == 0

    printed = capsys.readouterr().out
    excluded = load_excluded_task_ids(tmp_path / "consumed.json")
    assert "draw:       fresh small_test, seed 1" in printed
    assert f"2 id(s), sha256 {excluded.sha256}" in printed
    assert "provenance: derived" in printed
    assert before_after_split.draws == [FreshTestDraw(seed=1, excluded=excluded)]


def test_without_either_flag_the_split_loads_with_no_draw_and_the_plan_names_none(
    tmp_path: Path,
    stub_command: FakeArmRun,
    before_after_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo)) == 0

    printed = capsys.readouterr().out
    assert "draw:" not in printed and "excluded:" not in printed
    assert before_after_split.draws == [None]


def test_exclusions_without_a_seed_are_an_input_error(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    argv = before_after_argv(tmp_path, repo, "--exclude-task-ids", str(_exclusion(tmp_path)))

    assert main(argv) == 2

    assert "--split-seed" in capsys.readouterr().err


def test_the_plan_written_beside_a_run_carries_the_draw(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(_draw_argv(tmp_path, repo, "--confirm-spend")) == 0

    written = (tmp_path / "out" / "plan.txt").read_text(encoding="utf-8")
    assert "seed 1" in written and "provenance: derived" in written


def test_each_arms_settings_carry_the_draw_with_its_ids_frozen(
    tmp_path: Path, stub_command: FakeArmRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    plans: list[object] = []
    monkeypatch.setattr(
        before_after_command, "render_plan", lambda plan: plans.append(plan) or "plan"
    )
    argv = _draw_argv(tmp_path, repo)
    assert main(argv) == 0
    args = argparse.Namespace(max_usd=None, base_url=None, config=tmp_path / "serve.yaml")

    settings = arm_settings_for(args, plans[0], role=ArmRole.BASELINE, arm_dir=tmp_path)  # type: ignore[arg-type]

    reloaded = ArmSettings.from_dict(json.loads(json.dumps(asdict(settings))))
    assert reloaded.task_draw == plans[0].task_draw  # type: ignore[attr-defined]
    assert reloaded.task_draw is not None and reloaded.task_draw.excluded is not None
    assert reloaded.task_draw.excluded.task_ids == ("c1", "c2")


def _arm_settings(tmp_path: Path, draw: FreshTestDraw | None = None) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="a" * 40,
        workspace="/ws",
        model="m",
        trace_root="/t",
        out_dir=str(tmp_path),
        max_agent_turns=4,
        estimated_usd_per_rollout=0.0,
        cost_ceiling_usd=1.0,
        task_draw=draw,
    )


def test_an_arm_settings_file_without_a_draw_loads_as_no_draw(tmp_path: Path) -> None:
    settings = _arm_settings(tmp_path)
    write_arm_settings(tmp_path, settings)
    payload = json.loads((tmp_path / "arm_settings.json").read_text(encoding="utf-8"))
    payload.pop("task_draw", None)
    (tmp_path / "arm_settings.json").write_text(json.dumps(payload), encoding="utf-8")

    assert read_arm_settings(tmp_path).task_draw is None


def test_the_arm_child_loads_the_draw_its_settings_carry(
    tmp_path: Path, before_after_split: FakeSplitTasks, monkeypatch: pytest.MonkeyPatch
) -> None:
    draw = FreshTestDraw(seed=1, excluded=load_excluded_task_ids(_exclusion(tmp_path)))
    write_arm_settings(tmp_path, _arm_settings(tmp_path, draw))
    monkeypatch.setattr(before_after_command, "assert_product_under", lambda path: None)
    monkeypatch.setattr(before_after_command, "run_arm", _no_rollouts)

    code = main(
        [
            "before-after-arm",
            "--settings",
            str(tmp_path / "arm_settings.json"),
            "--split",
            "repoqa-qa/small_test",
            "--expect-product-under",
            str(tmp_path),
        ]
    )

    assert code == 0
    assert before_after_split.draws == [draw]


async def _no_rollouts(settings: ArmSettings, tasks: object) -> ArmSummary:
    return ArmSummary(
        role=settings.role,
        commit=settings.commit,
        model=settings.model,
        trace_root=settings.trace_root,
        tasks=[],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
    )


def test_the_reports_provenance_names_the_draw() -> None:
    excluded = ExcludedTaskIds(
        source="c.json", sha256="f" * 64, provenance="p", task_ids=("a", "b")
    )
    draw = FreshTestDraw(seed=1, excluded=excluded)
    arm = ArmMetrics(commit=_BASELINE, per_task=())

    with_draw = render_report(_plan(task_draw=draw), [arm, arm])
    without = render_report(_plan(), [arm, arm])

    assert "- fresh draw: seed 1, 2 id(s) excluded" in with_draw
    assert "fresh draw" not in without


# --- write-references ---------------------------------------------------------


def test_write_references_loads_the_draw_its_flags_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydocs_eval.campaign.reference_writer_command import cmd_write_references

    from .test_reference_writer_command import _args, _fixture_tasks

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    draws: list[object] = []

    async def loader(split: str, *, limit: int | None = None, draw: object = None) -> object:
        draws.append(draw)
        return await _fixture_tasks(split, limit=limit)

    exclusion = str(_exclusion(tmp_path))
    args = _args(tmp_path, "--split-seed", "1", "--exclude-task-ids", exclusion)
    args.split = "repoqa-qa/small_test"

    assert cmd_write_references(args, load_tasks=loader) == 0  # type: ignore[arg-type]

    assert draws == [FreshTestDraw(seed=1, excluded=load_excluded_task_ids(Path(exclusion)))]
    assert "draw:       fresh small_test, seed 1" in capsys.readouterr().out
