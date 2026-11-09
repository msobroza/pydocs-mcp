"""``before-after`` into an ``--out`` that already holds another run's arm: refused first.

The arm child refuses rows another run answered, but the parent used to write
the NEW run's ``arm_settings.json`` and ``serving.json`` before spawning it — so
a refused resume left the old rows beside new settings, and the compare verb
and ``--report-only`` then scored those answers under the wrong cap, gold or
workspace. The parent now refuses before any write to an arm directory.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_arm_launch
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import (
    ARM_SETTINGS_FILENAME,
    ArmTaskRecord,
    read_arm_summary,
    write_arm_summary,
)
from pydocs_eval.campaign.before_after_serving import SERVING_FINGERPRINT_FILENAME

from ._fakes import before_after_argv, git_repo_with_two_descriptions
from .test_before_after_arm_settings import write_overlay

_ROLES = ("baseline", "candidate")


def _arm_files(out: Path) -> dict[str, bytes]:
    """Every arm's settings and fingerprint, byte for byte."""
    return {
        f"{role}/{name}": (out / role / name).read_bytes()
        for role in _ROLES
        for name in (ARM_SETTINGS_FILENAME, SERVING_FINGERPRINT_FILENAME)
    }


def _never_spawned(*args: object, **kwargs: object) -> None:
    pytest.fail("a refused resume must never spawn the arm child")


@pytest.fixture
def finished_out(tmp_path: Path, real_launch: None) -> tuple[Path, dict[str, bytes]]:
    """A finished run's ``--out``, its arm files as they were, and no child from here on."""
    repo = git_repo_with_two_descriptions(tmp_path)
    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0
    return repo, _arm_files(tmp_path / "out")


def _rerun(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    monkeypatch.setattr(before_after_arm_launch, "_spawn_arm", _never_spawned)
    return main(before_after_argv(tmp_path, repo, "--confirm-spend", *extra))


def test_a_refused_resume_leaves_the_arm_settings_untouched(
    tmp_path: Path,
    finished_out: tuple[Path, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, before = finished_out
    overlay = write_overlay(tmp_path, {"seed_search_with_question": True}, "seed_on.yaml")

    assert _rerun(tmp_path, repo, monkeypatch, "--candidate-settings", str(overlay)) == 2

    error = capsys.readouterr().err
    recorded = read_arm_summary(tmp_path / "out" / "candidate").settings_digest
    assert f"settings_digest: recorded {recorded!r}, this arm '" in error
    assert _arm_files(tmp_path / "out") == before


def test_a_resume_under_another_model_names_both_models(
    tmp_path: Path,
    finished_out: tuple[Path, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, before = finished_out
    argv = before_after_argv(tmp_path, repo, "--confirm-spend")
    argv[argv.index("--model") + 1] = "another-model"
    monkeypatch.setattr(before_after_arm_launch, "_spawn_arm", _never_spawned)

    assert main(argv) == 2

    assert "model: recorded 'test-model', this arm 'another-model'" in capsys.readouterr().err
    assert _arm_files(tmp_path / "out") == before


def test_a_resume_under_a_shorter_limit_names_the_rows_it_would_drop(
    tmp_path: Path,
    finished_out: tuple[Path, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The paid row for ``t2`` would vanish from ``arm.json`` at the first checkpoint."""
    repo, before = finished_out
    arm_dir = tmp_path / "out" / "candidate"
    row = ArmTaskRecord("t2", "traj-t2", str(arm_dir), ["a.py"], 2, 1.0, 9)
    write_arm_summary(arm_dir, dataclasses.replace(read_arm_summary(arm_dir), tasks=[row]))

    assert _rerun(tmp_path, repo, monkeypatch, "--limit", "1") == 2

    error = capsys.readouterr().err
    assert "1 task(s) outside" in error and "'t2'" in error and "--out" in error
    assert _arm_files(tmp_path / "out") == before


def test_a_matching_resume_still_runs_both_arms(
    tmp_path: Path, finished_out: tuple[Path, dict[str, bytes]]
) -> None:
    repo, before = finished_out

    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0

    assert _arm_files(tmp_path / "out") == before
