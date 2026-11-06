"""The Cloud Build submission for one image flavour at one commit, as the port receives it.

The shape follows the commit's own ``infra/bench-vm/cloudbuild.yaml``: the
build source is a directory holding exactly ``commit.tar`` (a ``git archive``
of the commit, never the working tree), the config is that commit's
``cloudbuild.yaml``, and three substitutions name the commit, the flavour and
the Artifact Registry repository. The build runs in the deployment's region
(``europe-west4``), which the recipe's first step checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.vm_request import ImageFlavour

COMMIT_ARCHIVE_NAME = "commit.tar"
SUBSTITUTION_COMMIT = "_COMMIT"
SUBSTITUTION_FLAVOUR = "_FLAVOUR"
SUBSTITUTION_REPOSITORY = "_REPOSITORY"
# How long the launcher waits for one build. The recipe's own `timeout: 7200s`
# ends a hung build first; the margin covers the queue and the source upload.
IMAGE_BUILD_WAIT_S = 9000.0


@dataclass(frozen=True, slots=True)
class ImageBuildRequest:
    """One Cloud Build build of ``image`` (``<repository>/bench-<flavour>:<commit>``)."""

    project: str
    region: str
    image: str
    # A directory holding exactly `commit.tar`, the build's whole source.
    source_dir: Path
    # The run commit's `cloudbuild.yaml`, outside `source_dir`.
    config: Path
    substitutions: Mapping[str, str]
    wait_timeout_s: float


def image_build_substitutions(
    repository: str, flavour: ImageFlavour, commit: str
) -> dict[str, str]:
    """The ``--substitutions`` the recipe reads: the commit, the flavour, the repository."""
    return {
        SUBSTITUTION_COMMIT: commit,
        SUBSTITUTION_FLAVOUR: str(flavour),
        SUBSTITUTION_REPOSITORY: repository,
    }
