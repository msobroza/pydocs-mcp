"""The files a run commit must carry, as repository-relative paths read with ``git`` at the commit.

Everything on a bench VM comes from the run's commit, never the laptop's
working tree: the image recipe Cloud Build uses, and the host startup and
shutdown scripts the launcher passes as instance metadata. A commit without
them predates the bench VM tooling; older product commits are measured through
the ``before_after`` kind from a current harness commit instead.
"""

from __future__ import annotations

from pydocs_eval.bench_vm.vm_request import ImageFlavour

CLOUDBUILD_CONFIG = "infra/bench-vm/cloudbuild.yaml"
_DOCKERFILE_TEMPLATE = "infra/bench-vm/docker/Dockerfile.{flavour}"
# The host scripts the supervisor work ships; the launcher reads them at the
# commit and sends their text as the startup-script / shutdown-script metadata.
HOST_STARTUP_SCRIPT = "infra/bench-vm/host/startup.sh"
HOST_SHUTDOWN_SCRIPT = "infra/bench-vm/host/shutdown.sh"


def recipe_paths(flavour: ImageFlavour) -> tuple[str, ...]:
    """The recipe files a build of ``flavour`` reads from the commit."""
    return (CLOUDBUILD_CONFIG, _DOCKERFILE_TEMPLATE.format(flavour=flavour))
