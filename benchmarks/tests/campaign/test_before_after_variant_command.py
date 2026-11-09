"""``before-after`` with two arms on ONE commit that differ only by their overlay.

What an operator depends on: the plan prints both digests and the sorted diff;
the same commit under the same settings is refused unless ``--allow-identical``
says it is the A/A arm; each arm's settings file carries ITS overlay; and a
plan without any overlay prints exactly what it printed before overlays existed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm_launch import arm_settings_for

from ._fakes import FakeArmRun, before_after_argv, git_repo_with_two_descriptions
from .test_before_after_arm_settings import SEED_SKILL, write_overlay


def _same_commit_argv(tmp_path: Path, repo: Path, *extra: str) -> list[str]:
    return before_after_argv(tmp_path, repo, "--baseline", "HEAD", *extra)


def _seed_variant(tmp_path: Path) -> Path:
    return write_overlay(
        tmp_path, {"seed_search_with_question": True, "max_agent_turns": 2}, "seed_on.yaml"
    )


def test_two_overlays_on_one_commit_plan_with_their_digests_and_sorted_diff(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    candidate = _seed_variant(tmp_path)

    assert main(_same_commit_argv(tmp_path, repo, "--candidate-settings", str(candidate))) == 0

    printed = capsys.readouterr().out
    assert "settings:   baseline   " in printed and f"({candidate})" in printed
    diff = printed[printed.index("diff (baseline -> candidate):") :]
    assert diff.index("max_agent_turns: 4 -> 2") < diff.index(
        "seed_search_with_question: (default) -> True"
    )
    assert stub_command.roles == []


def test_one_commit_under_one_setting_is_refused_by_name(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(_same_commit_argv(tmp_path, repo, "--confirm-spend")) == 2

    assert "--allow-identical" in capsys.readouterr().err
    assert stub_command.roles == []


def test_allow_identical_runs_the_aa_arm(tmp_path: Path, stub_command: FakeArmRun) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(_same_commit_argv(tmp_path, repo, "--allow-identical", "--confirm-spend")) == 0

    assert stub_command.roles == ["baseline", "candidate"]


def test_two_overlays_on_one_commit_run_both_arms(tmp_path: Path, stub_command: FakeArmRun) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    argv = _same_commit_argv(
        tmp_path, repo, "--candidate-settings", str(_seed_variant(tmp_path)), "--confirm-spend"
    )

    assert main(argv) == 0

    assert stub_command.roles == ["baseline", "candidate"]


def test_identical_overlays_on_one_commit_are_refused_too(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    overlay = str(_seed_variant(tmp_path))
    argv = _same_commit_argv(
        tmp_path, repo, "--baseline-settings", overlay, "--candidate-settings", overlay
    )

    assert main(argv) == 2

    assert "--allow-identical" in capsys.readouterr().err


def test_a_plan_without_overlays_prints_no_settings_lines(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """Byte-identity for every run that names no overlay."""
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo)) == 0

    assert "settings:" not in capsys.readouterr().out


def test_an_invalid_overlay_is_an_input_error_naming_its_flag(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    overlay = write_overlay(tmp_path, {"guidance_task": "repo_qa"})

    assert main(before_after_argv(tmp_path, repo, "--baseline-settings", str(overlay))) == 2

    assert "--baseline-settings" in capsys.readouterr().err


def test_each_arms_settings_file_carries_its_own_variant(
    tmp_path: Path, stub_command: FakeArmRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Captures the plan the command built, then builds each arm's settings from it."""
    from pydocs_eval.campaign import before_after_command

    plans: list[object] = []
    monkeypatch.setattr(before_after_command, "render_plan", lambda plan: plans.append(plan) or "")
    repo = git_repo_with_two_descriptions(tmp_path)
    candidate = write_overlay(
        tmp_path,
        {"architecture": "auto", "guidance_sections": str(SEED_SKILL)},
        "auto.yaml",
    )

    assert main(_same_commit_argv(tmp_path, repo, "--candidate-settings", str(candidate))) == 0

    args = argparse.Namespace(max_usd=None, base_url=None, config=tmp_path / "serve.yaml")
    baseline, after = (
        arm_settings_for(args, plans[0], role=role, arm_dir=tmp_path / role)  # type: ignore[arg-type]
        for role in ("baseline", "candidate")
    )
    assert (baseline.architecture, after.architecture) == ("text_react", "auto")
    assert after.guidance_sections_path == str(SEED_SKILL) and after.guidance_sections
    assert baseline.guidance_sections == {}
    assert baseline.settings_digest != after.settings_digest
    assert json.loads(json.dumps(after.guidance_sections)) == after.guidance_sections
