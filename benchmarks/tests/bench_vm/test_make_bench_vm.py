"""``make bench-vm RUN=runs/<name>.yaml [DRY_RUN=1]`` runs the launcher like the other benchmark commands."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from ._spec_dicts import REPO_ROOT

_MAKEFILE = REPO_ROOT / "Makefile"
_LAUNCH = "PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm launch"

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _make_dry(*variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-n", "--no-print-directory", "bench-vm", *variables],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_makefile_declares_the_bench_vm_target_over_the_launcher() -> None:
    text = _MAKEFILE.read_text(encoding="utf-8")

    assert "\nbench-vm:" in text
    assert "-m pydocs_eval.bench_vm launch" in text
    assert "--dry-run" in text
    assert "DRY_RUN" in text


@needs_make
def test_make_bench_vm_launches_the_spec_under_infra_bench_vm() -> None:
    completed = _make_dry("RUN=runs/cpu-smoke.yaml")

    assert completed.returncode == 0, completed.stderr
    assert f"{_LAUNCH} infra/bench-vm/runs/cpu-smoke.yaml" in completed.stdout
    assert "--dry-run" not in completed.stdout


@needs_make
def test_dry_run_1_maps_to_the_launchers_dry_run_flag() -> None:
    completed = _make_dry("RUN=runs/cpu-smoke.yaml", "DRY_RUN=1")

    assert completed.returncode == 0, completed.stderr
    assert f"{_LAUNCH} infra/bench-vm/runs/cpu-smoke.yaml --dry-run" in completed.stdout


@needs_make
def test_make_bench_vm_without_run_fails_with_its_usage() -> None:
    completed = subprocess.run(
        ["make", "--no-print-directory", "bench-vm"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode != 0
    assert "RUN=runs/<name>.yaml" in completed.stderr
