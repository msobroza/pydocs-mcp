"""``--report-only`` re-renders a finished run under the settings its arms RECORDED.

It used to rebuild each arm's variant from the CLI flags. A same-commit variant
run then could not be re-rendered without its overlay flags (the identical-arms
guard refused it), and repeating them probed each arm in a worktree — a re-render
that "checks out nothing" checked out two. Now each arm's variant comes from its
``arm_settings.json``; an overlay flag, if given, must name the digest that arm
recorded; nothing is probed; and the plan the run wrote before its arms stays.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import read_arm_settings

from ._fakes import before_after_argv, git_repo_with_two_descriptions
from .test_before_after_arm_settings import write_overlay

_FLAG = "--candidate-settings"


def _never_probed(*args: object, **kwargs: object) -> object:
    pytest.fail("--report-only must never probe an arm: the arms already ran")


def _same_commit_argv(tmp_path: Path, repo: Path, *extra: str) -> list[str]:
    return before_after_argv(tmp_path, repo, "--baseline", "HEAD", *extra)


@pytest.fixture
def variant_run(tmp_path: Path, real_launch: None, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A finished same-commit run whose candidate ran a seed overlay; no probe from here on."""
    repo = git_repo_with_two_descriptions(tmp_path)
    overlay = write_overlay(
        tmp_path, {"seed_search_with_question": True, "max_agent_turns": 2}, "seed_on.yaml"
    )
    argv = _same_commit_argv(tmp_path, repo, _FLAG, str(overlay), "--confirm-spend")
    assert main(argv) == 0
    monkeypatch.setattr(before_after_command, "probe_arm_block", _never_probed)
    return repo


def test_report_only_rerenders_a_same_commit_variant_run_without_its_flags(
    tmp_path: Path, variant_run: Path
) -> None:
    out = tmp_path / "out"
    plan_before = (out / "plan.txt").read_bytes()

    assert main(_same_commit_argv(tmp_path, variant_run, "--report-only")) == 0

    recorded = read_arm_settings(out / "candidate").settings_digest
    report = (out / "before_after.md").read_text(encoding="utf-8")
    [line] = [line for line in report.splitlines() if line.startswith("- settings, candidate:")]
    assert f"digest `{recorded[:12]}" in line
    assert (out / "plan.txt").read_bytes() == plan_before


def test_report_only_accepts_the_overlay_flag_the_arm_ran(
    tmp_path: Path, variant_run: Path
) -> None:
    overlay = tmp_path / "seed_on.yaml"

    argv = _same_commit_argv(tmp_path, variant_run, _FLAG, str(overlay), "--report-only")

    assert main(argv) == 0


def test_report_only_refuses_an_overlay_flag_naming_other_settings(
    tmp_path: Path, variant_run: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = write_overlay(tmp_path, {"max_agent_turns": 3}, "other.yaml")
    recorded = read_arm_settings(tmp_path / "out" / "candidate").settings_digest
    report_before = (tmp_path / "out" / "before_after.md").read_bytes()

    argv = _same_commit_argv(tmp_path, variant_run, _FLAG, str(other), "--report-only")

    assert main(argv) == 2
    error = capsys.readouterr().err
    assert recorded in error and str(other) in error and _FLAG in error
    assert (tmp_path / "out" / "before_after.md").read_bytes() == report_before
