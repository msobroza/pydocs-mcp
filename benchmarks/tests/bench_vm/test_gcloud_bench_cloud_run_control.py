"""``GcloudBenchCloud``'s calls for status, stop and pull: log tails, prefix downloads, VM listing.

Nothing here runs ``gcloud``: a ``ScriptedCommandRunner`` replays its answers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, LabelledInstance
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud

from ._fakes import ScriptedCommandRunner, failed, ok

_LOG_URI = "gs://bench-runs/cpu-smoke-0123abcd/state/logs/child-attempt-1.log"
_PREFIX_URI = "gs://bench-runs/cpu-smoke-0123abcd/"
_TRANSIENT = "ERROR: HTTPError 503: Service Unavailable"


def _cloud(*script: CompletedCommand | str) -> tuple[GcloudBenchCloud, ScriptedCommandRunner]:
    runner = ScriptedCommandRunner(script=list(script))
    return GcloudBenchCloud(runner=runner, sleep=lambda _s: None), runner


# --- object tails ----------------------------------------------------------------


def test_a_tail_read_asks_gcloud_for_the_last_bytes_only() -> None:
    cloud, runner = _cloud(ok("line 9\nline 10\n"))

    tail = cloud.read_object_tail(_LOG_URI, max_bytes=4096)

    assert tail == b"line 9\nline 10\n"
    argv = runner.calls[0].argv
    assert argv[:3] == ("gcloud", "storage", "cat")
    assert "--range=-4096" in argv
    assert _LOG_URI in argv
    assert 0 < runner.calls[0].timeout_s < 600


def test_a_tail_read_of_a_missing_object_is_none() -> None:
    cloud, _ = _cloud(failed("ERROR: (gcloud.storage.cat) The following URLs matched no objects"))

    assert cloud.read_object_tail(_LOG_URI, max_bytes=4096) is None


def test_a_tail_read_of_an_empty_object_is_empty() -> None:
    cloud, _ = _cloud(failed("ERROR: HTTPError 416: The requested range cannot be satisfied."))

    assert cloud.read_object_tail(_LOG_URI, max_bytes=4096) == b""


def test_a_tail_read_retries_a_transient_error_then_fails_naming_the_object() -> None:
    cloud, runner = _cloud(failed(_TRANSIENT), ok("x\n"))
    assert cloud.read_object_tail(_LOG_URI, max_bytes=10) == b"x\n"
    assert len(runner.calls) == 2

    broken, _ = _cloud(failed("ERROR: 403 caller does not have storage.objects.get"))
    with pytest.raises(BenchCloudError, match=re.escape(_LOG_URI)):
        broken.read_object_tail(_LOG_URI, max_bytes=10)


# --- prefix downloads ------------------------------------------------------------


def test_a_prefix_download_is_a_recursive_rsync_into_the_local_directory(tmp_path: Path) -> None:
    cloud, runner = _cloud(ok())
    destination = tmp_path / "results" / "cpu-smoke-0123abcd"

    cloud.download_prefix(_PREFIX_URI, destination)

    argv = runner.calls[0].argv
    assert argv[:5] == ("gcloud", "storage", "rsync", _PREFIX_URI, str(destination))
    assert "--recursive" in argv
    assert destination.is_dir()
    assert runner.calls[0].timeout_s > 0


def test_a_prefix_download_never_deletes_local_files(tmp_path: Path) -> None:
    cloud, runner = _cloud(ok())

    cloud.download_prefix(_PREFIX_URI, tmp_path)

    assert not any("delete" in arg for arg in runner.calls[0].argv)


def test_a_prefix_download_retries_a_transient_error_then_fails_naming_the_prefix(
    tmp_path: Path,
) -> None:
    cloud, runner = _cloud(failed(_TRANSIENT), ok())
    cloud.download_prefix(_PREFIX_URI, tmp_path)
    assert len(runner.calls) == 2

    broken, _ = _cloud(failed("ERROR: 403 caller does not have storage.objects.list"))
    with pytest.raises(BenchCloudError, match=re.escape(_PREFIX_URI)):
        broken.download_prefix(_PREFIX_URI, tmp_path)


# --- every VM carrying a label -----------------------------------------------------


def _instance(name: str, labels: dict[str, str] | None) -> dict[str, object]:
    item: dict[str, object] = {
        "name": name,
        "zone": "https://www.googleapis.com/compute/v1/projects/p/zones/europe-west4-b",
        "status": "RUNNING",
    }
    if labels is not None:
        item["labels"] = labels
    return item


def test_labelled_instances_are_every_vm_carrying_the_label_whatever_its_value() -> None:
    answer = [
        _instance("bench-a-1", {"bench-run-id": "a", "bench-attempt": "1"}),
        _instance("bench-b-2", {"bench-run-id": "b", "bench-attempt": "2"}),
        _instance("someone-else", {"team": "x"}),
        _instance("unlabelled", None),
    ]
    cloud, runner = _cloud(ok(json.dumps(answer)))

    listed = cloud.list_labelled_instances(
        "bench-project", ("europe-west4-a", "europe-west4-b"), label="bench-run-id"
    )

    assert listed == (
        LabelledInstance(
            name="bench-a-1",
            zone="europe-west4-b",
            status="RUNNING",
            labels={"bench-run-id": "a", "bench-attempt": "1"},
        ),
        LabelledInstance(
            name="bench-b-2",
            zone="europe-west4-b",
            status="RUNNING",
            labels={"bench-run-id": "b", "bench-attempt": "2"},
        ),
    )
    argv = runner.calls[0].argv
    assert argv[:4] == ("gcloud", "compute", "instances", "list")
    assert "--project=bench-project" in argv
    assert "--zones=europe-west4-a,europe-west4-b" in argv
    assert "--filter=labels.bench-run-id:*" in argv


def test_labelled_listing_retries_a_transient_error_and_reports_others() -> None:
    cloud, runner = _cloud(failed(_TRANSIENT), ok("[]"))
    assert cloud.list_labelled_instances("p", ("z",), label="bench-run-id") == ()
    assert len(runner.calls) == 2

    broken, _ = _cloud(failed("ERROR: (gcloud.compute.instances.list) PERMISSION_DENIED"))
    with pytest.raises(BenchCloudError, match="PERMISSION_DENIED"):
        broken.list_labelled_instances("p", ("z",), label="bench-run-id")
