"""before/after's baseline and candidate must be on the product repository the VM clones (#424, #430).

The VM clones ``PRODUCT_REPO_URL``, so a commit only a fork holds would pass
every laptop check and fail on the VM after the build and boot spend. Driven
through the entry point against ``FakeBenchCloud`` and the real fixture git
repository, whose ``origin`` is configured with the product URL; a ``fork``
remote is a second local bare repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.product_repo import PRODUCT_REPO_URL
from pydocs_eval.bench_vm.run_record import RunStatus, run_record_bytes

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR, FixtureRepo, point_url_at, run_git
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import before_after_spec, retrieval_spec

Capsys = pytest.CaptureFixture[str]
_FORK_URL = "git@github.com:someone-else/pydocs-mcp.git"


def _add_fork(repo: FixtureRepo, root: Path, name: str = "fork") -> None:
    bare = root / f"{name.replace('/', '-')}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    run_git(repo.path, "remote", "add", name, _FORK_URL)
    point_url_at(repo.path, _FORK_URL, bare)


def _commit_on_the_fork_only(repo: FixtureRepo, root: Path, name: str = "fork") -> str:
    """A commit pushed to remote ``name`` and nowhere else; local ``main`` stays at ``origin/main``."""
    _add_fork(repo, root, name)
    pushed = repo.head()
    repo.write("product.txt", "fork only\n")
    commit = repo.commit("a fork's candidate")
    run_git(repo.path, "push", "-q", name, "HEAD:refs/heads/feature")
    run_git(repo.path, "reset", "-q", "--hard", pushed)
    return commit


def _fork_candidate_spec(harness: LaunchHarness, candidate: str) -> dict[str, object]:
    return before_after_spec(
        configs=[FIXTURE_CONFIG], baseline_ref=harness.repo.head(), candidate_ref=candidate
    )


def _assert_refused_before_any_write(harness: LaunchHarness, result: LaunchResult) -> None:
    assert result.exit_code == 1
    assert harness.writes() == []
    assert "create_instance" not in harness.cloud.operations()


@pytest.mark.parametrize("field", ["candidate_ref", "baseline_ref"])
def test_a_before_after_commit_only_a_fork_holds_is_refused_naming_commit_remotes_and_url(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path, field: str
) -> None:
    fork_only = _commit_on_the_fork_only(harness.repo, tmp_path)
    refs = {"baseline_ref": harness.repo.head(), "candidate_ref": "main", field: fork_only}
    spec = before_after_spec(configs=[FIXTURE_CONFIG], **refs)

    result = harness.launch(capsys, "ba", spec)

    _assert_refused_before_any_write(harness, result)
    assert field in result.err
    assert fork_only in result.err
    assert "origin" in result.err and "fork" in result.err, "the remotes checked"
    assert PRODUCT_REPO_URL in result.err, "the URL the VM clones"


def test_a_product_remote_spelled_in_ssh_form_holds_the_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    ssh_url = "git@github.com:msobroza/pydocs-mcp.git"
    run_git(harness.repo.path, "remote", "set-url", "origin", ssh_url)
    point_url_at(harness.repo.path, ssh_url, harness.repo.origin)
    baseline = harness.repo.head()
    harness.repo.write("product.txt", "newer\n")
    candidate = harness.repo.commit("candidate")
    harness.repo.push_main()
    harness.publish_images(candidate)
    spec = before_after_spec(configs=[FIXTURE_CONFIG], baseline_ref=baseline, candidate_ref="main")

    result = harness.launch(capsys, "ba", spec)

    assert result.exit_code == 0, result.err
    record = harness.record(result.run_id)
    assert (record.baseline_commit, record.candidate_commit) == (baseline, candidate)


def test_a_retrieval_run_commit_may_live_on_any_remote(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    """Only before/after clones the product repository; other kinds' commits ship in the image."""
    _add_fork(harness.repo, tmp_path)
    harness.repo.write("notes/fork.txt", "fork\n")
    commit = harness.repo.commit("a fork's run commit")
    run_git(harness.repo.path, "push", "-q", "fork", "HEAD:refs/heads/feature")
    harness.publish_images(commit)
    spec = retrieval_spec(configs=[FIXTURE_CONFIG], corpus_dir=FIXTURE_CORPUS_DIR, git_ref=commit)

    result = harness.launch(capsys, "cpu-smoke", spec)

    assert result.exit_code == 0, result.err
    assert harness.record(result.run_id).commit == commit


def test_only_a_remotes_fetch_url_counts_not_a_second_push_url(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    """git fetches from a remote's FIRST url, so a fork with the product as a second url is a fork."""
    fork_only = _commit_on_the_fork_only(harness.repo, tmp_path)
    run_git(harness.repo.path, "remote", "set-url", "--add", "fork", PRODUCT_REPO_URL)

    result = harness.launch(capsys, "ba", _fork_candidate_spec(harness, fork_only))

    _assert_refused_before_any_write(harness, result)
    assert fork_only in result.err


def test_a_remote_named_under_the_product_remotes_name_is_not_the_product_remote(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    """``refs/remotes/origin/fork/feature`` is the ``origin/fork`` remote's branch, not ``origin``'s."""
    fork_only = _commit_on_the_fork_only(harness.repo, tmp_path, name="origin/fork")

    result = harness.launch(capsys, "ba", _fork_candidate_spec(harness, fork_only))

    _assert_refused_before_any_write(harness, result)
    assert "origin/fork" in result.err


def test_a_product_remote_spelled_through_an_insteadof_shorthand_holds_the_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    run_git(harness.repo.path, "remote", "set-url", "origin", "gh:msobroza/pydocs-mcp")
    run_git(harness.repo.path, "config", "url.https://github.com/.insteadOf", "gh:")
    point_url_at(harness.repo.path, "gh:msobroza/pydocs-mcp", harness.repo.origin)
    baseline = harness.repo.head()
    harness.repo.write("product.txt", "newer\n")
    candidate = harness.repo.commit("candidate")
    harness.repo.push_main()
    harness.publish_images(candidate)
    spec = before_after_spec(configs=[FIXTURE_CONFIG], baseline_ref=baseline, candidate_ref="main")

    result = harness.launch(capsys, "ba", spec)

    assert result.exit_code == 0, result.err


def test_a_relaunch_refuses_a_recorded_before_after_commit_only_a_fork_holds(
    harness: LaunchHarness, capsys: Capsys, tmp_path: Path
) -> None:
    """A record frozen before the product-remote rule is checked again on relaunch."""
    first = harness.launch(capsys, "ba", _fork_candidate_spec(harness, "main"))
    assert first.exit_code == 0, first.err
    harness.supervisor_records(first.run_id, RunStatus.FAILED)
    fork_only = _commit_on_the_fork_only(harness.repo, tmp_path)
    older = harness.record(first.run_id).model_copy(update={"candidate_commit": fork_only})
    harness.cloud.put_object(harness.record_uri(first.run_id), run_record_bytes(older))
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    _assert_refused_before_any_write(harness, second)
    assert "candidate_commit" in second.err
    assert fork_only in second.err
