"""``make bench-vm-status|stop|pull`` call the run-control verbs, naming the run by its spec."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from ._spec_dicts import REPO_ROOT

_VERB = "PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm"
_SPEC = "infra/bench-vm/runs/cpu-smoke.yaml"

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_makefile_declares_the_run_control_targets_as_phony() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    for target in ("bench-vm-status", "bench-vm-stop", "bench-vm-pull"):
        assert f"\n{target}:" in text
        assert f" {target}" in text.split("\n\n", 1)[0], f"{target} is not in .PHONY"


@needs_make
@pytest.mark.parametrize("verb", ["status", "stop", "pull"])
def test_each_target_names_its_run_by_the_spec_under_infra_bench_vm(verb: str) -> None:
    completed = _make("-n", f"bench-vm-{verb}", "RUN=runs/cpu-smoke.yaml")

    assert completed.returncode == 0, completed.stderr
    assert f"{_VERB} {verb} {_SPEC}" in completed.stdout


@needs_make
def test_status_without_run_lists_every_live_bench_vm() -> None:
    completed = _make("-n", "bench-vm-status")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith(f"{_VERB} status")


@needs_make
@pytest.mark.parametrize("verb", ["stop", "pull"])
def test_stop_and_pull_without_run_fail_with_their_usage(verb: str) -> None:
    completed = _make(f"bench-vm-{verb}")

    assert completed.returncode != 0
    assert f"make bench-vm-{verb} RUN=runs/<name>.yaml" in completed.stderr
