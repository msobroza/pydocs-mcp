"""``launch`` refusals: everything that can fail on the laptop fails before anything is written or created."""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.commit_layout import HOST_STARTUP_SCRIPT, recipe_paths
from pydocs_eval.bench_vm.vm_request import ImageFlavour

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR, run_git
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import before_after_spec, retrieval_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _assert_refused_before_any_write(harness: LaunchHarness, result: LaunchResult) -> None:
    assert result.exit_code == 1
    assert harness.writes() == []
    assert harness.cloud.created == []
    assert "create_instance" not in harness.cloud.operations()


# --- commits -----------------------------------------------------------------


def test_a_commit_not_on_any_remote_tracking_branch_is_refused(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    unpushed = harness.repo.commit_unpushed("local only")
    harness.publish_images(unpushed)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    _assert_refused_before_any_write(harness, result)
    assert "git_ref" in result.err
    assert unpushed in result.err
    assert "remote-tracking branch" in result.err


def test_a_ref_that_does_not_resolve_is_refused_naming_it(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(git_ref="no-such-branch"))

    _assert_refused_before_any_write(harness, result)
    assert "'no-such-branch'" in result.err


def test_a_config_missing_at_the_commit_is_refused_naming_path_and_commit(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    # Present in the working tree, absent at the commit: the commit is what runs.
    harness.repo.write("benchmarks/configs/uncommitted.yaml", "name: x\n")

    result = harness.launch(
        capsys, "cpu-smoke", _spec(configs=["benchmarks/configs/uncommitted.yaml"])
    )

    _assert_refused_before_any_write(harness, result)
    assert "benchmarks/configs/uncommitted.yaml" in result.err
    assert harness.repo.head() in result.err


def test_a_corpus_dir_missing_at_the_commit_is_refused_naming_path_and_commit(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(corpus_dir="benchmarks/fixtures/nowhere"))

    _assert_refused_before_any_write(harness, result)
    assert "benchmarks/fixtures/nowhere" in result.err
    assert harness.repo.head() in result.err


def test_a_corpus_dir_that_is_a_file_at_the_commit_is_refused(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(corpus_dir=FIXTURE_CONFIG))

    _assert_refused_before_any_write(harness, result)
    assert "a directory" in result.err


def test_a_run_commit_without_the_recipe_is_refused_pointing_at_before_after(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    (cloudbuild, *_) = recipe_paths(ImageFlavour.CPU)
    harness.repo.remove(cloudbuild)
    commit = harness.repo.commit("drop the recipe")
    harness.repo.push_main()
    harness.publish_images(commit)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    _assert_refused_before_any_write(harness, result)
    assert cloudbuild in result.err
    assert "before_after" in result.err


def test_a_run_commit_without_a_host_script_is_refused_naming_it(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.repo.remove(HOST_STARTUP_SCRIPT)
    commit = harness.repo.commit("drop the startup script")
    harness.repo.push_main()
    harness.publish_images(commit)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    _assert_refused_before_any_write(harness, result)
    assert HOST_STARTUP_SCRIPT in result.err


def test_before_after_freezes_the_baseline_and_candidate_commits(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    baseline = harness.repo.head()
    run_git(harness.repo.path, "tag", "v-old")
    run_git(harness.repo.path, "push", "-q", "origin", "v-old")
    harness.repo.write("product.txt", "newer\n")
    candidate = harness.repo.commit("candidate")
    harness.repo.push_main()
    harness.publish_images(candidate)
    spec = before_after_spec(configs=[FIXTURE_CONFIG], baseline_ref=baseline, candidate_ref="main")

    result = harness.launch(capsys, "ba", spec)

    assert result.exit_code == 0, result.err
    record = harness.record(result.run_id)
    assert (record.commit, record.baseline_commit, record.candidate_commit) == (
        candidate,
        baseline,
        candidate,
    )
    assert (record.baseline_ref, record.candidate_ref) == (baseline, "main")


def test_before_after_refuses_an_unreachable_baseline(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    pushed = harness.repo.head()
    unpushed = harness.repo.commit_unpushed("local baseline")
    run_git(harness.repo.path, "reset", "-q", "--hard", pushed)
    spec = before_after_spec(configs=[FIXTURE_CONFIG], baseline_ref=unpushed, candidate_ref="main")

    result = harness.launch(capsys, "ba", spec)

    _assert_refused_before_any_write(harness, result)
    assert "baseline_ref" in result.err
    assert unpushed in result.err


# --- machine type ------------------------------------------------------------


@pytest.mark.parametrize(
    ("machine_type", "named"),
    [
        ("n2-standard-4", "4 vCPU"),
        ("n1-standard-8", "30720 MB"),
        ("t2a-standard-8", "ARM64"),
        ("e2-standard-8", "no architecture"),
        ("z9-standard-8", "no such machine type"),
    ],
)
def test_a_machine_type_under_the_floor_or_not_x86_64_is_refused_before_any_write(
    harness: LaunchHarness, capsys: Capsys, machine_type: str, named: str
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(machine_type=machine_type))

    _assert_refused_before_any_write(harness, result)
    assert machine_type in result.err
    assert named in result.err


def test_a_machine_type_above_the_floor_is_accepted(harness: LaunchHarness, capsys: Capsys) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(machine_type="n2-highmem-8"))

    assert result.exit_code == 0, result.err
    assert [request.machine_type for request in harness.cloud.created] == ["n2-highmem-8"]
