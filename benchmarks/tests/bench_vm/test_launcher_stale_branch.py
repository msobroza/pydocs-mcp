"""A ref naming a local branch must sit where its remote-tracking branch does, at a first launch.

The launch is offline and never fetches, so a local ``main`` left behind
``origin/main`` would otherwise freeze an old commit into the run record for
good (every relaunch reuses it). Driven through the entry point against
``FakeBenchCloud`` and the real fixture git repository, whose ``main`` has no
configured upstream (the fixture pushes without ``-u``), so ``origin/main`` is
its counterpart by name.
"""

from __future__ import annotations

import pytest

from ._git_fixture import (
    FIXTURE_CONFIG,
    FIXTURE_CORPUS_DIR,
    FixtureRepo,
    run_git,
    write_run_commit_layout,
)
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _fall_behind(repo: FixtureRepo) -> tuple[str, str]:
    """Leave local ``main`` one commit behind ``origin/main``; return (local, remote) commits."""
    local = repo.head()
    write_run_commit_layout(repo, marker="second")
    remote = repo.commit("newer on the remote")
    repo.push_main()
    run_git(repo.path, "reset", "-q", "--hard", local)
    return local, remote


def _refusal(harness: LaunchHarness, result: LaunchResult) -> str:
    """The refusal's text alone: the spec path before it holds the test's name."""
    assert result.exit_code == 1
    assert harness.writes() == []
    assert "create_instance" not in harness.cloud.operations()
    _, marker, refusal = result.err.partition("launch refused: ")
    assert marker, result.err
    return refusal


def test_a_local_branch_behind_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, remote = _fall_behind(harness.repo)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert refusal.startswith("git_ref: got 'main'")
    assert local in refusal and remote in refusal
    assert "1 commit behind its remote-tracking branch origin/main" in refusal


def test_a_local_branch_ahead_of_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    remote = harness.repo.head()
    local = harness.repo.commit_unpushed("local only")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert local in refusal and remote in refusal
    assert "1 commit ahead of its remote-tracking branch origin/main" in refusal


def test_a_local_branch_diverged_from_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    _, remote = _fall_behind(harness.repo)
    local = harness.repo.commit_unpushed("local only")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert local in refusal and remote in refusal
    assert "diverged (1 commit ahead, 1 behind) from its remote-tracking branch origin/main" in (
        refusal
    )
