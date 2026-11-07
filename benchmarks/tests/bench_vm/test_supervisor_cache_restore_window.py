"""The cache restore's window: it starts transfers within a share of the time left to the soft deadline.

The soft deadline counts from the attempt's ``launching`` record, so the VM's
boot and image pull, and a relaunch's state restore, have already spent part
of it when the cache restore begins. The committed smoke specs, an hour and two
hours long, still restore.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._spec_dicts import COMMITTED_RUNS_DIR, retrieval_spec
from ._supervise_harness import SuperviseHarness

_WEIGHTS_URI = "gs://bench-cache/shared/fastembed/m.onnx"


def _cache_restores(harness: SuperviseHarness) -> list[str]:
    return [
        source
        for source, _ in harness.launch.cloud.restores
        if source.startswith("gs://bench-cache/")
    ]


def _spend(harness: SuperviseHarness, minutes: int) -> None:
    """Move the launch clock on, as a boot or a restore spends real time."""
    clock = harness.launch.clock
    clock.now += timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("spec_name", "boot"),
    # Boot plus the image pull: a CPU image in minutes, a CUDA image of several GB longer.
    [("cpu-smoke", timedelta(minutes=3)), ("gpu-smoke", timedelta(minutes=10))],
)
def test_the_committed_smoke_specs_restore_their_caches(
    supervise_harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    spec_name: str,
    boot: timedelta,
) -> None:
    supervise_harness.launch.cloud.put_object(_WEIGHTS_URI, b"onnx")
    smoke = load_run_spec(COMMITTED_RUNS_DIR / f"{spec_name}.yaml").spec
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(
        capsys, run_id, timing=SupervisorTiming.for_spec(smoke, elapsed=boot)
    )

    assert result.exit_code == 0, result.err
    assert "home/.cache/fastembed/m.onnx" in result.started["cache_files"]
    assert "bench_vm_cache_transfer_deferred" not in result.err
    assert supervise_harness.record(run_id).cache_bytes_restored == len(b"onnx")


def test_the_boot_s_time_counts_against_the_cache_restore_s_share(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.launch.cloud.put_object(_WEIGHTS_URI, b"onnx")
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec(max_runtime="1h"))
    _spend(supervise_harness, 40)  # past the hour's soft deadline (34 minutes) before boot ends

    result = supervise_harness.supervise(capsys, run_id, timing=None)

    assert _cache_restores(supervise_harness) == []
    assert "bench_vm_cache_transfer_deferred" in result.err
    assert supervise_harness.record(run_id).status is RunStatus.TIMED_OUT


def test_the_state_restore_s_time_counts_against_the_cache_restore_s_share(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec(max_runtime="1h"))
    supervise_harness.supervise(capsys, run_id, FIXTURE_EXIT_CODE="3")  # attempt 1 fails
    supervise_harness.relaunch(capsys, "cpu-smoke")
    supervise_harness.boot_a_fresh_vm()
    cloud.put_object(_WEIGHTS_URI, b"onnx")
    # Attempt 2's state restore takes 50 minutes of the hour's soft deadline (34 minutes).
    cloud.restore_hooks[supervise_harness.state_uri(run_id)] = lambda: _spend(supervise_harness, 50)

    result = supervise_harness.supervise(capsys, run_id, timing=None)

    assert _cache_restores(supervise_harness) == []
    assert "bench_vm_cache_transfer_deferred" in result.err
    assert supervise_harness.record(run_id).status is RunStatus.TIMED_OUT
