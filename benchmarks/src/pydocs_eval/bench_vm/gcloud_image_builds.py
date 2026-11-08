"""The image builds of ``GcloudBenchCloud``: submit a Cloud Build ``--async``, then poll it.

A mixin, like ``GcloudCacheTransfers``, so ``gcloud_bench_cloud`` keeps one
adapter class. The submission returns as soon as Cloud Build has the build,
so no single ``gcloud`` call outlives its own timeout, and the poll has a
deadline of its own (``ImageBuildRequest.wait_timeout_s``): a build still
running at the deadline is cancelled, so it neither bills on nor pushes later.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, ImageBuildError
from pydocs_eval.bench_vm.gcloud_calls import GCLOUD_READ_TIMEOUT_S, GcloudCalls
from pydocs_eval.bench_vm.gcloud_json import json_object
from pydocs_eval.bench_vm.image_build_request import ImageBuildRequest

# Submitting uploads the commit archive before Cloud Build answers with the build.
_BUILD_SUBMIT_TIMEOUT_S = 900.0
_BUILD_POLL_INTERVAL_S = 15.0


class CloudBuildStatus(StrEnum):
    """Cloud Build's build statuses, in its spelling."""

    STATUS_UNKNOWN = "STATUS_UNKNOWN"
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    WORKING = "WORKING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


_BUILD_STILL_RUNNING = frozenset(
    {
        CloudBuildStatus.STATUS_UNKNOWN,
        CloudBuildStatus.PENDING,
        CloudBuildStatus.QUEUED,
        CloudBuildStatus.WORKING,
    }
)


class GcloudImageBuilds(GcloudCalls):
    """The image-build operation of the ``BenchCloud`` port, over ``gcloud builds``."""

    __slots__ = ()

    sleep: Callable[[float], None]  # GcloudBenchCloud provides both clocks
    monotonic: Callable[[], float]

    def build_image(self, request: ImageBuildRequest) -> None:
        # A submission retried after a transient error can start a second build
        # of the same tag; the immutable tag makes the duplicate harmless.
        build = self._submit_build(request)
        build_id = str(build["id"])
        deadline = self.monotonic() + request.wait_timeout_s
        while True:
            status = _build_status(build)
            if status is CloudBuildStatus.SUCCESS:
                return
            if status not in _BUILD_STILL_RUNNING:
                raise _build_error(request, build, status, "the build did not succeed")
            if self.monotonic() >= deadline:
                raise _build_error(request, build, status, self._cancel_build(request, build_id))
            self.sleep(_BUILD_POLL_INTERVAL_S)
            build = self._describe_build(request, build_id)

    def _cancel_build(self, request: ImageBuildRequest, build_id: str) -> str:
        """Cancel a build the launcher gave up on, so it neither bills on nor pushes later."""
        waited = f"still running after waiting {request.wait_timeout_s:.0f}s"
        argv = (
            "builds",
            "cancel",
            build_id,
            f"--region={request.region}",
            f"--project={request.project}",
        )
        try:
            completed = self._gcloud(argv, GCLOUD_READ_TIMEOUT_S)
        except BenchCloudError as exc:
            return f"{waited}; cancelling it failed: {exc}"
        if completed.returncode != 0:
            return f"{waited}; cancelling it failed: {completed.stderr_text}"
        return f"{waited}; cancelled"

    def _submit_build(self, request: ImageBuildRequest) -> dict[str, Any]:
        substitutions = ",".join(f"{key}={value}" for key, value in request.substitutions.items())
        argv = (
            "builds",
            "submit",
            str(request.source_dir),
            f"--config={request.config}",
            f"--region={request.region}",
            f"--project={request.project}",
            f"--substitutions={substitutions}",
            "--async",
            "--format=json",
        )
        completed = self._gcloud(argv, _BUILD_SUBMIT_TIMEOUT_S)
        if completed.returncode != 0:
            raise ImageBuildError(
                request.image,
                build_id=None,
                log_url=None,
                status="NOT_SUBMITTED",
                detail=f"gcloud builds submit exited {completed.returncode}: {completed.stderr_text}",
            )
        build = json_object(completed.stdout, f"submitting the build of {request.image}")
        if not build.get("id"):
            raise BenchCloudError(
                f"submitting the build of {request.image}: got {build!r}, expected a build id"
            )
        return build

    def _describe_build(self, request: ImageBuildRequest, build_id: str) -> dict[str, Any]:
        argv = (
            "builds",
            "describe",
            build_id,
            f"--region={request.region}",
            f"--project={request.project}",
            "--format=json",
        )
        completed = self._gcloud(argv, GCLOUD_READ_TIMEOUT_S)
        self._raise_unless_ok(completed, f"describing build {build_id}")
        return json_object(completed.stdout, f"describing build {build_id}")


def _build_status(build: Mapping[str, Any]) -> CloudBuildStatus:
    status = build.get("status")
    try:
        return CloudBuildStatus(str(status))
    except ValueError:
        # Failing now beats polling an unknown status for the whole wait.
        raise BenchCloudError(
            f"build {build.get('id')}: got status {status!r}, expected one of "
            f"{', '.join(CloudBuildStatus)}"
        ) from None


def _build_error(
    request: ImageBuildRequest, build: Mapping[str, Any], status: CloudBuildStatus, detail: str
) -> ImageBuildError:
    log_url = build.get("logUrl")
    return ImageBuildError(
        request.image,
        build_id=str(build["id"]),
        log_url=log_url if isinstance(log_url, str) else None,
        status=str(status),
        detail=detail,
    )
