"""The pre-flight, before any spend: the GPU probe, the kind's own pre-flight, before/after's clone.

Driven through ``python -m pydocs_eval.bench_vm supervise`` against the fake
cloud, with ``_fixture_child.py`` as both the kind's pre-flight and its run.
A run whose pre-flight fails ends ``failed`` without the paid child ever
starting.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fakes import CPU_ONLY_PROBE, FakeGpuProbe
from ._fixture_child import PLAN_LINE
from ._git_fixture import FIXTURE_CONFIG, run_git
from ._spec_dicts import agent_track_spec, before_after_spec, retrieval_spec
from ._supervise_harness import FIXTURE_CHILD, SuperviseHarness, SuperviseResult


def _ending(harness: SuperviseHarness, run_id: str) -> tuple[RunStatus, str]:
    last = harness.record(run_id).history[-1]
    return last.status, last.detail


def _events(result: SuperviseResult, name: str) -> list[dict[str, object]]:
    events = [json.loads(line) for line in result.err.splitlines() if line.startswith("{")]
    return [event for event in events if event["event"] == name]


def _launch_before_after(
    harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> tuple[str, str, str]:
    repo = harness.launch.repo
    baseline = repo.head()
    run_git(repo.path, "tag", "v-old", baseline)
    repo.write("notes/candidate.txt", "candidate\n")
    candidate = repo.commit("candidate commit")
    repo.push_main()
    harness.launch.publish_images(candidate)
    spec = before_after_spec(
        configs=[FIXTURE_CONFIG], baseline_ref="v-old", args=["--model", "qwen/qwen3-4b"]
    )
    return harness.launch_run(capsys, "before-after", spec), baseline, candidate


# --- the GPU pre-flight ---------------------------------------------------------------


def test_a_gpu_run_whose_probe_finds_no_cuda_fails_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.gpu_probe = FakeGpuProbe(probe=CPU_ONLY_PROBE)
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))

    result = supervise_harness.supervise(capsys, run_id)

    status, detail = _ending(supervise_harness, run_id)
    assert result.exit_code == 1
    assert status is RunStatus.FAILED
    assert detail.startswith("GPU pre-flight failed: ")
    assert "torch 2.5.1+cpu sees no CUDA device" in detail
    assert "expected 'CUDAExecutionProvider' among them" in detail
    assert result.invocations == []
    # The ending is an ending like any other: synced, recorded, VM deleted.
    assert supervise_harness.launch.cloud.deleted


def test_a_gpu_run_whose_probe_cannot_run_fails_naming_the_probe(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.gpu_probe = FakeGpuProbe(error="the GPU probe exited 139: segfault")
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))

    result = supervise_harness.supervise(capsys, run_id)

    status, detail = _ending(supervise_harness, run_id)
    assert status is RunStatus.FAILED
    assert detail == "GPU pre-flight failed: the GPU probe exited 139: segfault"
    assert result.invocations == []


def test_a_gpu_run_whose_probe_sees_cuda_runs_its_child(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    assert supervise_harness.gpu_probe.calls == 1
    assert len(_events(result, "bench_vm_gpu_preflight_passed")) == 1


def test_the_probe_asks_the_interpreter_the_child_runs_on(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    # The supervisor's ports put the fixture in place of the image's python.
    assert supervise_harness.gpu_probe.asked == [(sys.executable, str(FIXTURE_CHILD))]


def test_a_preflight_that_uses_up_the_soft_deadline_times_out_without_the_child(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.gpu_probe = FakeGpuProbe(delay_s=0.6)
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))
    timing = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=0.3, stop_grace_s=5.0)

    result = supervise_harness.supervise(capsys, run_id, timing=timing)

    status, detail = _ending(supervise_harness, run_id)
    assert status is RunStatus.TIMED_OUT
    assert detail.startswith("soft deadline of 0.3s passed during the pre-flight")
    assert result.invocations == []


def test_the_kind_preflight_stops_at_the_soft_deadline_left_after_the_earlier_steps(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())
    timing = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=1.0, stop_grace_s=5.0)

    result = supervise_harness.supervise(
        capsys, run_id, timing=timing, FIXTURE_PREFLIGHT_SLEEP_S="30"
    )

    status, detail = _ending(supervise_harness, run_id)
    assert status is RunStatus.FAILED
    assert re.match(r"the kind's pre-flight did not finish within 0\.\d+s", detail), detail
    assert result.signals == ["stopped"]


def test_a_cpu_run_never_probes_the_gpu(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.gpu_probe = FakeGpuProbe(probe=CPU_ONLY_PROBE)
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    assert supervise_harness.gpu_probe.calls == 0


def test_a_kind_without_a_preflight_runs_only_its_child(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id)

    assert len(result.invocations) == 1
    assert _events(result, "bench_vm_kind_preflight_passed") == []


# --- the agent track's --preflight ------------------------------------------------------


def test_the_agent_track_runs_its_preflight_before_the_paid_child(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = agent_track_spec(dataset="swe-qa", args=["--max-tasks", "3"])
    run_id = supervise_harness.launch_run(capsys, "agents", spec)

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    preflight, child = (invocation["argv"] for invocation in result.invocations)
    state = supervise_harness.state_dir
    assert preflight == [
        "-m",
        "pydocs_eval.agent_track",
        "--preflight",
        "--dataset",
        "swe-qa",
        "--max-usd",
        "20.0",
        "--ledger",
        str(state / "agent_track_pairs.jsonl"),
        "--report",
        str(state / "report.md"),
        "--max-tasks",
        "3",
    ]
    assert "--preflight" not in child
    assert result.invocations[0]["cwd"] == str(supervise_harness.repo_dir)


def test_a_failing_preflight_ends_the_run_failed_with_no_spend(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_PREFLIGHT_EXIT_CODE="1")

    status, detail = _ending(supervise_harness, run_id)
    assert result.exit_code == 1
    assert status is RunStatus.FAILED
    assert detail.startswith("the kind's pre-flight exited with exit code 1; ")
    assert "logs/preflight-attempt-1.log" in detail
    # Only the pre-flight ran: the paid child never started.
    assert [("--preflight" in i["argv"]) for i in result.invocations] == [True]
    assert supervise_harness.record(run_id).exit_code is None


def test_a_preflight_that_hangs_is_stopped_and_fails_the_run(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    result = supervise_harness.supervise(
        capsys, run_id, preflight_timeout_s=0.5, FIXTURE_PREFLIGHT_SLEEP_S="30"
    )

    status, detail = _ending(supervise_harness, run_id)
    assert status is RunStatus.FAILED
    assert detail.startswith("the kind's pre-flight did not finish within 0.5s")
    assert result.signals == ["stopped"]
    assert len(result.invocations) == 1


# --- before/after: the plan-only invocation and the product clone -------------------------


def test_before_after_runs_its_plan_only_before_the_paid_child(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, _, _ = _launch_before_after(supervise_harness, capsys)

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0, result.err
    plan, child = (invocation["argv"] for invocation in result.invocations)
    assert "--confirm-spend" not in plan
    assert [arg for arg in child if arg != "--confirm-spend"] == plan
    assert child.count("--confirm-spend") == 1


def test_the_plan_output_is_logged_and_kept_in_the_state(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, _, _ = _launch_before_after(supervise_harness, capsys)

    result = supervise_harness.supervise(capsys, run_id)

    (passed,) = _events(result, "bench_vm_kind_preflight_passed")
    assert PLAN_LINE in str(passed["output"])
    log = supervise_harness.state_dir / "logs" / "preflight-attempt-1.log"
    assert PLAN_LINE in log.read_text(encoding="utf-8")
    synced = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + "logs/preflight-attempt-1.log"
    )
    assert synced is not None and PLAN_LINE.encode() in synced


def test_before_afters_repo_is_a_blobless_clone_holding_both_frozen_commits(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, baseline, candidate = _launch_before_after(supervise_harness, capsys)

    result = supervise_harness.supervise(capsys, run_id)

    clone = supervise_harness.product_clone
    child = result.started["argv"]
    assert child[child.index("--repo") + 1] == str(clone)
    assert run_git(clone, "config", "remote.origin.partialclonefilter") == "blob:none"
    for sha in (baseline, candidate):
        subprocess.run(["git", "-C", str(clone), "cat-file", "-e", f"{sha}^{{commit}}"], check=True)


def test_a_product_clone_that_fails_ends_the_run_before_the_plan(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, _, _ = _launch_before_after(supervise_harness, capsys)
    origin = supervise_harness.launch.repo.origin
    origin.rename(origin.with_name("moved.git"))  # the remote is unreachable

    result = supervise_harness.supervise(capsys, run_id)

    status, detail = _ending(supervise_harness, run_id)
    assert status is RunStatus.FAILED
    assert detail.startswith("the product clone failed: ")
    assert result.invocations == []
