"""Ensure the run commit's image exists: look its tag up, and build it only when it is missing.

A launch needs one flavour, ``bench-cpu:<sha>`` or ``bench-cuda:<sha>``, and
builds only that one. The build is a Cloud Build build in the deployment's
region from a ``git archive`` of the commit, with that commit's own
``cloudbuild.yaml`` — never the working tree's — so recipe and code always come
from one commit.

Two launches racing on one commit are both fine: the repository's tags are
immutable, so the losing build's push is refused, and a failed build whose tag
exists afterwards is the race lost, not a failure. A build that failed with
no tag behind it fails the launch, before any run record is written.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, ImageBuildError
from pydocs_eval.bench_vm.commit_layout import CLOUDBUILD_CONFIG
from pydocs_eval.bench_vm.deployment import DeploymentNames
from pydocs_eval.bench_vm.image_build_request import (
    COMMIT_ARCHIVE_NAME,
    IMAGE_BUILD_WAIT_S,
    ImageBuildRequest,
    image_build_substitutions,
)
from pydocs_eval.bench_vm.launch_events import log_launch_event
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.vm_request import ImageFlavour, image_tag_name

_CONFIG_FILE_NAME = "cloudbuild.yaml"
_SOURCE_DIR_NAME = "source"


def ensure_run_image(
    cloud: BenchCloud, git: GitCheckout, names: DeploymentNames, flavour: ImageFlavour, commit: str
) -> str:
    """The ``sha256:…`` digest of the commit's ``flavour`` image, building it when it is missing."""
    image = image_tag_name(names.image_repository, flavour, commit)
    digest = cloud.find_image_digest(image)
    if digest is not None:
        return digest
    log_launch_event("bench_vm_image_build_started", image=image, commit=commit)
    try:
        _build_from_commit(cloud, git, names, flavour, commit)
    except ImageBuildError as exc:
        return _digest_after_a_lost_race(cloud, image, exc)
    return _digest_after_a_build(cloud, image)


def _build_from_commit(
    cloud: BenchCloud, git: GitCheckout, names: DeploymentNames, flavour: ImageFlavour, commit: str
) -> None:
    with tempfile.TemporaryDirectory(prefix="bench-vm-build-") as scratch:
        root = Path(scratch)
        source_dir = root / _SOURCE_DIR_NAME
        source_dir.mkdir()
        git.archive_to(commit, source_dir / COMMIT_ARCHIVE_NAME)
        # Outside the source dir: the recipe expects a source holding exactly commit.tar.
        config = root / _CONFIG_FILE_NAME
        config.write_text(git.read_file_at(commit, CLOUDBUILD_CONFIG), encoding="utf-8")
        cloud.build_image(
            ImageBuildRequest(
                project=names.project_id,
                region=names.region,
                image=image_tag_name(names.image_repository, flavour, commit),
                source_dir=source_dir,
                config=config,
                substitutions=image_build_substitutions(names.image_repository, flavour, commit),
                wait_timeout_s=IMAGE_BUILD_WAIT_S,
            )
        )


def _digest_after_a_lost_race(cloud: BenchCloud, image: str, failure: ImageBuildError) -> str:
    """A failed build whose tag exists lost the immutable-tag race; anything else fails."""
    digest = cloud.find_image_digest(image)
    if digest is None:
        raise failure
    log_launch_event(
        "bench_vm_image_build_lost_race", image=image, build_id=failure.build_id, digest=digest
    )
    return digest


def _digest_after_a_build(cloud: BenchCloud, image: str) -> str:
    digest = cloud.find_image_digest(image)
    if digest is None:
        raise BenchCloudError(
            f"image {image}: got no tag after a successful build, expected the tag the "
            "build pushed; check the build's images section"
        )
    log_launch_event("bench_vm_image_built", image=image, digest=digest)
    return digest
