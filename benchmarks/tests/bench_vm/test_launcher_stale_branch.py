"""A ref naming a local branch must sit where its remote-tracking branch does, at a first launch.

The launch is offline and never fetches, so a local ``main`` left behind
``origin/main`` would otherwise freeze an old commit into the run record for
good (every relaunch reuses it). Driven through the entry point against
``FakeBenchCloud`` and the real fixture git repository, whose ``main`` has no
configured upstream (the fixture pushes without ``-u``), so ``origin/main`` is
its counterpart by name.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus

from ._git_fixture import (
    FIXTURE_CONFIG,
    FIXTURE_CORPUS_DIR,
    FixtureRepo,
    add_bare_remote,
    run_git,
)
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import before_after_spec, retrieval_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _get_ahead(repo: FixtureRepo) -> tuple[str, str]:
    """Give local ``main`` a commit ``origin/main`` lacks; return (local, remote)."""
    remote = repo.head()
    return repo.commit_unpushed("local only"), remote


def _diverge(repo: FixtureRepo) -> tuple[str, str]:
    """Leave local ``main`` one commit behind ``origin/main`` and one ahead; return (local, remote)."""
    _, remote = repo.fall_behind_origin()
    return repo.commit_unpushed("local only"), remote


def _refusal(harness: LaunchHarness, result: LaunchResult) -> str:
    """The refusal's text alone: the spec path before it holds the test's name."""
    assert result.exit_code == 1
    assert harness.writes() == []
    assert "create_instance" not in harness.cloud.operations()
    _, marker, refusal = result.err.partition("launch refused: ")
    assert marker, result.err
    return refusal


def _frozen_commit(harness: LaunchHarness, result: LaunchResult) -> str:
    assert result.exit_code == 0, result.err
    return harness.record(result.run_id).commit


# --- a local branch out of step with its remote-tracking branch is refused -------


def test_a_local_branch_behind_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, remote = harness.repo.fall_behind_origin()

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert refusal.startswith("git_ref: got 'main'")
    assert local in refusal and remote in refusal
    assert "1 commit behind its remote-tracking branch origin/main" in refusal


def test_a_local_branch_ahead_of_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, remote = _get_ahead(harness.repo)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert local in refusal and remote in refusal
    assert "1 commit ahead of its remote-tracking branch origin/main" in refusal


def test_a_local_branch_diverged_from_its_remote_tracking_branch_is_refused_naming_both_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, remote = _diverge(harness.repo)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert local in refusal and remote in refusal
    assert "diverged (1 commit ahead, 1 behind) from its remote-tracking branch origin/main" in (
        refusal
    )


@pytest.mark.parametrize("ref", ["heads/main", "refs/heads/main", "HEAD"])
def test_every_spelling_of_the_local_branch_is_checked(
    harness: LaunchHarness, capsys: Capsys, ref: str
) -> None:
    local, remote = harness.repo.fall_behind_origin()

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref=ref))

    refusal = _refusal(harness, result)
    assert refusal.startswith(f"git_ref: got {ref!r}, local branch main at commit {local}")
    assert remote in refusal


# --- a ref that names no local branch is taken as written ------------------------


def test_a_local_branch_level_with_its_remote_tracking_branch_is_frozen(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert _frozen_commit(harness, result) == harness.repo.head()


def test_a_tag_is_taken_as_written_while_the_local_branch_is_behind(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, _ = harness.repo.fall_behind_origin()
    run_git(harness.repo.path, "tag", "v-older", local)

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref="v-older"))

    assert _frozen_commit(harness, result) == local


def test_a_commit_sha_is_taken_as_written_while_the_local_branch_is_behind(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, _ = harness.repo.fall_behind_origin()

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref=local))

    assert _frozen_commit(harness, result) == local


def test_an_explicit_remote_tracking_branch_is_taken_as_written_while_the_local_one_is_behind(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    _, remote = harness.repo.fall_behind_origin()

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref="origin/main"))

    assert _frozen_commit(harness, result) == remote


def test_a_local_branch_with_no_remote_tracking_counterpart_keeps_the_reachability_rule(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    pushed = harness.repo.head()
    run_git(harness.repo.path, "branch", "reachable", pushed)
    run_git(harness.repo.path, "checkout", "-q", "-b", "unpushed")
    unpushed = harness.repo.commit_unpushed("only here")
    run_git(harness.repo.path, "checkout", "-q", "main")

    accepted = harness.launch(capsys, "cpu-smoke", _spec(git_ref="reachable"))
    refused = harness.launch(capsys, "unpushed", _spec(git_ref="unpushed"))

    assert _frozen_commit(harness, accepted) == pushed
    assert refused.exit_code == 1
    assert f"got 'unpushed' at commit {unpushed}" in refused.err
    assert "expected a commit reachable from a remote-tracking branch" in refused.err


# --- which remote-tracking branch is the counterpart -----------------------------


def test_a_configured_upstream_is_the_counterpart_whatever_its_name(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    local, remote = harness.repo.fall_behind_origin()
    run_git(harness.repo.path, "branch", "work", local)
    run_git(harness.repo.path, "branch", "-q", "--set-upstream-to=origin/main", "work")

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref="work"))

    refusal = _refusal(harness, result)
    assert refusal.startswith(f"git_ref: got 'work', local branch work at commit {local}")
    assert f"its remote-tracking branch origin/main at commit {remote}" in refusal


def test_a_configured_upstream_wins_over_a_same_named_remote_tracking_branch(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    run_git(harness.repo.path, "push", "-q", "origin", "main:release")
    local, _ = harness.repo.fall_behind_origin()
    run_git(harness.repo.path, "branch", "-q", "--set-upstream-to=origin/release", "main")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert _frozen_commit(harness, result) == local


def test_a_configured_upstream_since_pruned_falls_back_to_the_same_named_branch(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    """As after a remote renamed the branch: ``main`` still tracks the gone ``origin/old``."""
    run_git(harness.repo.path, "push", "-q", "origin", "main:old")
    run_git(harness.repo.path, "branch", "-q", "--set-upstream-to=origin/old", "main")
    run_git(harness.repo.path, "push", "-q", "origin", "--delete", "old")
    local, remote = harness.repo.fall_behind_origin()

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert f"local branch main at commit {local}" in refusal
    assert f"its remote-tracking branch origin/main at commit {remote}" in refusal


def test_a_remote_tracking_branch_below_the_name_is_not_its_counterpart(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    """``origin/rel/next`` is another branch, not ``rel``'s: git keeps no ``origin/rel`` beside it."""
    pushed = harness.repo.head()
    elsewhere = harness.repo.commit_unpushed("on rel/next")
    run_git(harness.repo.path, "push", "-q", "origin", "HEAD:refs/heads/rel/next")
    run_git(harness.repo.path, "reset", "-q", "--hard", pushed)
    run_git(harness.repo.path, "branch", "rel", pushed)

    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref="rel"))

    assert _frozen_commit(harness, result) == pushed != elsewhere


def test_without_an_upstream_every_remotes_same_named_branch_must_agree(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    add_bare_remote(harness.repo, tmp_path, "fork", "git@github.com:someone-else/pydocs-mcp.git")
    level = harness.repo.head()
    fork_main = harness.repo.commit_unpushed("on the fork's main")
    run_git(harness.repo.path, "push", "-q", "fork", "HEAD:refs/heads/main")
    run_git(harness.repo.path, "reset", "-q", "--hard", level)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    refusal = _refusal(harness, result)
    assert f"its remote-tracking branch fork/main at commit {fork_main}" in refusal


# --- before/after's refs ---------------------------------------------------------


@pytest.mark.parametrize("field", ["baseline_ref", "candidate_ref"])
def test_a_before_after_ref_naming_a_stale_local_branch_is_refused_naming_its_field(
    harness: LaunchHarness, capsys: Capsys, field: str
) -> None:
    local, remote = harness.repo.fall_behind_origin()
    refs = {"baseline_ref": local, "candidate_ref": remote, field: "main"}
    spec = before_after_spec(configs=[FIXTURE_CONFIG], git_ref=remote, **refs)

    result = harness.launch(capsys, "ba", spec)

    refusal = _refusal(harness, result)
    assert refusal.startswith(f"{field}: got 'main', local branch main at commit {local}")
    assert remote in refusal


# --- a relaunch never checks the branch again --------------------------------------


@pytest.mark.parametrize(
    "drift",
    [
        pytest.param(FixtureRepo.fall_behind_origin, id="behind"),
        pytest.param(_get_ahead, id="ahead"),
        pytest.param(_diverge, id="diverged"),
    ],
)
def test_a_relaunch_reuses_the_recorded_commit_however_the_local_branch_moved_since(
    harness: LaunchHarness, capsys: Capsys, drift: Callable[[FixtureRepo], tuple[str, str]]
) -> None:
    first = harness.launch(capsys, "cpu-smoke", _spec())
    frozen = _frozen_commit(harness, first)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    drift(harness.repo)
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err
    record = harness.record(first.run_id)
    assert (record.commit, record.attempt) == (frozen, 2)
