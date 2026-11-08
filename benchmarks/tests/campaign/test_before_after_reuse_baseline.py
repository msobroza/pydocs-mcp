"""``--reuse-baseline <arm dir>``: compare a new candidate against a baseline already run.

The baseline arm is the expensive half every comparison of a ladder step
repeats. Reusing a finished one is sound only when it ran what THIS plan's
baseline would run — the same commit, model, turn cap and settings digest — so
any mismatch is refused by name, before a worktree or a token; a match runs the
candidate alone and links ``<out>/baseline`` to the reused arm, so the report,
``--report-only`` and the compare verb read it like an arm of this run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import ARM_SUMMARY_FILENAME, ArmSummary
from pydocs_eval.campaign.before_after_arm_variant import arm_variants
from pydocs_eval.campaign.before_after_reuse import BaselineIdentity, reusable_baseline

from ._fakes import FakeArmRun, before_after_argv, git_repo_with_two_descriptions

# What the ``stub_command`` fixture's serving settings decide for both arms.
_SERVING_TURNS = 4
_MODEL = "test-model"


def _sha(repo: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _default_digest() -> str:
    return arm_variants(None, None, llm_block=None, serving_turns=_SERVING_TURNS).baseline.digest


def write_finished_baseline(arm_dir: Path, **overrides: object) -> Path:
    """A finished baseline arm's ``arm.json``, matching the plan unless overridden."""
    fields: dict[str, object] = {
        "role": "baseline",
        "commit": "",
        "model": _MODEL,
        "trace_root": str(arm_dir / "traces"),
        "tasks": [],
        "estimated_usd": 0.0,
        "halt_reason": "completed",
        "excluded": 0,
        "max_agent_turns": _SERVING_TURNS,
        "settings_digest": _default_digest(),
        **overrides,
    }
    arm_dir.mkdir(parents=True, exist_ok=True)
    (arm_dir / ARM_SUMMARY_FILENAME).write_text(
        json.dumps(ArmSummary.from_dict(fields).to_dict()), encoding="utf-8"
    )
    return arm_dir


def _finished_baseline(tmp_path: Path, repo: Path, **overrides: object) -> Path:
    return write_finished_baseline(
        tmp_path / "earlier" / "baseline", **{"commit": _sha(repo, "HEAD~1"), **overrides}
    )


def test_a_matching_baseline_runs_the_candidate_alone(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _finished_baseline(tmp_path, repo)

    argv = before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused), "--confirm-spend")
    assert main(argv) == 0

    assert stub_command.roles == ["candidate"]
    linked = tmp_path / "out" / "baseline"
    assert linked.resolve() == reused.resolve()
    assert (tmp_path / "out" / "before_after.md").exists()


def test_the_plan_names_the_reused_baseline_and_prices_one_arm(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _finished_baseline(tmp_path, repo)

    assert main(before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused))) == 0

    printed = capsys.readouterr().out
    assert f"reused from {reused} (no baseline rollout)" in printed
    assert "estimate:   2 rollout(s)" in printed  # two tasks, the candidate alone


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit", "f" * 40),
        ("model", "another-model"),
        ("max_agent_turns", 9),
        ("settings_digest", "e" * 64),
    ],
)
def test_a_baseline_that_ran_something_else_is_refused_by_name(
    tmp_path: Path,
    stub_command: FakeArmRun,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _finished_baseline(tmp_path, repo, **{field: value})

    argv = before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused), "--confirm-spend")
    assert main(argv) == 2

    error = capsys.readouterr().err
    assert f"{field}: recorded {value!r}" in error and str(reused) in error
    assert stub_command.roles == []


def test_an_arm_written_before_settings_digests_is_refused(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _finished_baseline(tmp_path, repo, settings_digest="")

    assert main(before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused))) == 2

    assert "settings_digest: recorded ''" in capsys.readouterr().err


def test_a_directory_without_an_arm_is_refused_naming_it(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    assert main(before_after_argv(tmp_path, repo, "--reuse-baseline", str(empty))) == 2

    error = capsys.readouterr().err
    assert str(empty) in error and ARM_SUMMARY_FILENAME in error


def test_an_out_dir_already_holding_another_baseline_is_refused(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never overwrite an arm: the link would hide what that directory holds."""
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _finished_baseline(tmp_path, repo)
    write_finished_baseline(tmp_path / "out" / "baseline", commit=_sha(repo, "HEAD~1"))

    argv = before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused), "--confirm-spend")
    assert main(argv) == 2

    assert str(tmp_path / "out" / "baseline") in capsys.readouterr().err
    assert stub_command.roles == []


def test_reusing_the_out_dirs_own_baseline_needs_no_link(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    """Re-running a candidate against the baseline a killed run already finished."""
    repo = git_repo_with_two_descriptions(tmp_path)
    own = write_finished_baseline(tmp_path / "out" / "baseline", commit=_sha(repo, "HEAD~1"))

    argv = before_after_argv(tmp_path, repo, "--reuse-baseline", str(own), "--confirm-spend")
    assert main(argv) == 0

    assert stub_command.roles == ["candidate"]
    assert not own.is_symlink()


def test_the_identity_gate_accepts_an_arm_that_matches(tmp_path: Path) -> None:
    arm = write_finished_baseline(tmp_path / "arm", commit="a" * 40)
    expected = BaselineIdentity(
        commit="a" * 40,
        model=_MODEL,
        max_agent_turns=_SERVING_TURNS,
        settings_digest=_default_digest(),
    )

    assert reusable_baseline(arm, expected).commit == "a" * 40


def test_the_identity_gate_names_every_mismatch_at_once(tmp_path: Path) -> None:
    arm = write_finished_baseline(tmp_path / "arm", commit="a" * 40, model="m2")
    expected = BaselineIdentity(
        commit="b" * 40, model=_MODEL, max_agent_turns=_SERVING_TURNS, settings_digest="x"
    )

    with pytest.raises(MeasurementPlanError) as refused:
        reusable_baseline(arm, expected)

    message = str(refused.value)
    for field in ("commit", "model", "settings_digest"):
        assert f"{field}: recorded" in message
    assert "max_agent_turns" not in message
