"""``before-after`` with one commit as both arms: the A/A pair the benchmark README documents.

An A/A pair runs the same commit twice, so the difference between its arms is
noise. Nothing in the harness refuses identical arms today, and the README's
same-commit invocation depends on that. These tests pin it: a change that
starts refusing identical arms fails here first and must update the README's
A/A section in the same change.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_product import product_worktree

from ._fakes import FakeArmRun, before_after_argv, git_repo_with_two_descriptions


def _plan_line(plan_text: str, label: str) -> str:
    """The ``label`` line of ``plan.txt``, without its label."""
    [line] = [line for line in plan_text.splitlines() if line.startswith(f"{label}:")]
    return line.removeprefix(f"{label}:").strip()


def _head_sha(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def test_one_commit_runs_as_both_arms_of_a_confirmed_measurement(
    tmp_path: Path, stub_command: FakeArmRun
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    # argparse keeps the last value, so this --baseline overrides the argv's HEAD~1.
    argv = before_after_argv(tmp_path, repo, "--baseline", "HEAD", "--confirm-spend")

    assert main(argv) == 0

    assert stub_command.roles == ["baseline", "candidate"]
    plan_text = (tmp_path / "out" / "plan.txt").read_text(encoding="utf-8")
    sha12 = _head_sha(repo)[:12]
    assert _plan_line(plan_text, "baseline").split()[0] == sha12
    assert _plan_line(plan_text, "candidate").split()[0] == sha12
    assert (tmp_path / "out" / "before_after.md").exists()


def test_one_commits_arm_worktree_can_be_entered_again_after_the_first_arm_left_it(
    tmp_path: Path,
) -> None:
    """A/A only: the second arm adds a worktree at the path the first arm just removed."""
    repo = git_repo_with_two_descriptions(tmp_path)
    sha = _head_sha(repo)
    root = tmp_path / "arms"
    expected = root / f"src-{sha[:12]}"

    for _arm in ("A1", "A2"):
        with product_worktree(repo, sha, root) as path:
            assert path == expected
            assert path.is_dir()
        assert not path.exists()
