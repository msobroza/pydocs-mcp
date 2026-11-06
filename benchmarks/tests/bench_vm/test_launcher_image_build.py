"""``launch`` builds the run commit's image only when Artifact Registry lacks its tag.

Driven through the entry point against ``FakeBenchCloud`` and the real fixture
git repository; the fake reads each build's source while it exists, so these
tests see the ``git archive`` and the config a real build would upload.
"""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.commit_layout import CLOUDBUILD_CONFIG
from pydocs_eval.bench_vm.image_build_request import (
    COMMIT_ARCHIVE_NAME,
    IMAGE_BUILD_WAIT_S,
    SUBSTITUTION_COMMIT,
    SUBSTITUTION_FLAVOUR,
    SUBSTITUTION_REPOSITORY,
)
from pydocs_eval.bench_vm.vm_request import METADATA_IMAGE_DIGEST

from ._fakes import fake_built_digest
from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR
from ._launch_harness import LaunchHarness
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]
_REPOSITORY = "europe-west4-docker.pkg.dev/bench-project/bench"


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _image(flavour: str, commit: str) -> str:
    return f"{_REPOSITORY}/bench-{flavour}:{commit}"


def test_a_missing_tag_submits_one_build_of_the_commit_in_europe_west4(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    commit = harness.repo.head()

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    (build,) = harness.cloud.builds
    request = build.request
    assert (request.project, request.region) == ("bench-project", "europe-west4")
    assert request.image == _image("cpu", commit)
    assert dict(request.substitutions) == {
        SUBSTITUTION_COMMIT: commit,
        SUBSTITUTION_FLAVOUR: "cpu",
        SUBSTITUTION_REPOSITORY: _REPOSITORY,
    }
    assert request.wait_timeout_s == IMAGE_BUILD_WAIT_S
    assert build.source_files == (COMMIT_ARCHIVE_NAME,)
    assert build.archive_commit == commit
    assert CLOUDBUILD_CONFIG in build.archive_members


def test_the_build_uses_the_commits_cloudbuild_config_never_the_working_trees(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    harness.repo.write(CLOUDBUILD_CONFIG, "# EDITED, NOT COMMITTED\n")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    (build,) = harness.cloud.builds
    assert build.config_text == f"# {CLOUDBUILD_CONFIG} at first\n"
    assert not build.request.config.is_relative_to(build.request.source_dir)


def test_the_built_images_digest_reaches_the_record_and_the_vm(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    built = fake_built_digest(_image("cpu", harness.repo.head()))

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert harness.record(result.run_id).image_digest == built
    (vm,) = harness.cloud.created
    assert vm.metadata[METADATA_IMAGE_DIGEST] == built


def test_a_present_tag_builds_nothing_so_two_launches_of_one_commit_make_one_build(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()

    first = harness.launch(capsys, "cpu-smoke", _spec())
    second = harness.launch(capsys, "cpu-other", _spec(max_runtime="2h"))

    assert (first.exit_code, second.exit_code) == (0, 0), second.err
    assert first.run_id != second.run_id
    assert len(harness.cloud.builds) == 1
    assert harness.record(second.run_id).image_digest == harness.record(first.run_id).image_digest


def test_a_cpu_run_never_builds_the_cuda_image(harness: LaunchHarness, capsys: Capsys) -> None:
    harness.cloud.images.clear()

    harness.launch(capsys, "cpu-smoke", _spec())

    assert [build.request.image for build in harness.cloud.builds] == [
        _image("cpu", harness.repo.head())
    ]


def test_a_gpu_run_builds_only_the_cuda_image(harness: LaunchHarness, capsys: Capsys) -> None:
    harness.cloud.images.clear()

    result = harness.launch(capsys, "gpu-smoke", retrieval_spec(configs=[FIXTURE_CONFIG], gpu={}))

    assert result.exit_code == 0, result.err
    (build,) = harness.cloud.builds
    assert build.request.image == _image("cuda", harness.repo.head())
    assert build.request.substitutions[SUBSTITUTION_FLAVOUR] == "cuda"


def test_a_build_that_loses_the_immutable_tag_race_still_launches_on_the_winners_image(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    image = _image("cpu", harness.repo.head())
    harness.cloud.racing_builds.add(image)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    winner = fake_built_digest(image, builder="a rival launch")
    assert harness.record(result.run_id).image_digest == winner
    assert len(harness.cloud.created) == 1


def test_a_failed_build_fails_the_launch_before_any_record_naming_the_build_and_its_log(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    harness.cloud.failing_builds.add(_image("cpu", harness.repo.head()))

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert "launch failed" in result.err
    assert "fake-build-1" in result.err
    assert (
        "https://console.cloud.google.com/cloud-build/builds;region=europe-west4/fake-build-1"
        in (result.err)
    )
    assert harness.writes() == []
    assert harness.cloud.created == []


def test_a_run_commit_without_the_recipe_is_refused_before_any_build(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.images.clear()
    harness.repo.remove("infra/bench-vm")
    harness.repo.commit("an older product commit, before the bench VM tooling")
    harness.repo.push_main()

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert CLOUDBUILD_CONFIG in result.err
    assert "infra/bench-vm/docker/Dockerfile.cpu" in result.err
    assert "before_after" in result.err
    assert harness.cloud.builds == []
    assert harness.writes() == []
