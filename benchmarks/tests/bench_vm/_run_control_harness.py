"""Drive ``python -m pydocs_eval.bench_vm status|stop|pull`` in process against named fakes.

Runs are launched for real through ``LaunchHarness`` first, so the record, the
spec copy and the VM these verbs read are the ones the launcher wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
import pytest

from pydocs_eval.bench_vm.__main__ import main
from pydocs_eval.bench_vm.run_control import RunControlPorts

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]


@dataclass(frozen=True, slots=True)
class VerbResult:
    exit_code: int
    out: str
    err: str


def run_control(harness: LaunchHarness, capsys: Capsys, *argv: str) -> VerbResult:
    """Run one run-control verb over the harness's fake cloud and fake ``terraform output``."""
    capsys.readouterr()
    ports = RunControlPorts(cloud=harness.cloud, deployment_names=harness.names)
    exit_code = main(list(argv), run_control_ports=ports)
    captured = capsys.readouterr()
    return VerbResult(exit_code=exit_code, out=captured.out, err=captured.err)


def launch_fixture_run(
    harness: LaunchHarness, capsys: Capsys, stem: str = "cpu-smoke"
) -> LaunchResult:
    """Launch a retrieval run over the fixture repository; its VM is live afterwards."""
    spec = retrieval_spec(configs=[FIXTURE_CONFIG], corpus_dir=FIXTURE_CORPUS_DIR)
    launched = harness.launch(capsys, stem, spec)
    assert launched.exit_code == 0, launched.err
    return launched


def run_prefix(harness: LaunchHarness, run_id: str) -> str:
    return f"gs://{harness.names.names.runs_bucket}/{run_id}/"


def put_child_log(harness: LaunchHarness, run_id: str, attempt: int, text: str) -> None:
    """Stand in for the supervisor's sync of the child's log for ``attempt``."""
    uri = f"{run_prefix(harness, run_id)}state/logs/child-attempt-{attempt}.log"
    harness.cloud.put_object(uri, text.encode("utf-8"))
