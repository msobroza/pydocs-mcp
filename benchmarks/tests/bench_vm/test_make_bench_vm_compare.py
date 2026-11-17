"""``make bench-vm-compare`` calls the ``compare`` verb, naming the run by its spec like ``pull``."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from ._spec_dicts import REPO_ROOT

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


def test_the_makefile_declares_bench_vm_compare_as_phony() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "\nbench-vm-compare:" in text
    assert " bench-vm-compare" in text.split("\n\n", 1)[0], "bench-vm-compare is not in .PHONY"


@needs_make
def test_the_target_compares_the_run_named_by_the_spec_under_infra_bench_vm() -> None:
    completed = _make("-n", "bench-vm-compare", "RUN=runs/cpu-resume-smoke.yaml")

    assert completed.returncode == 0, completed.stderr
    assert (
        "PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm compare "
        "infra/bench-vm/runs/cpu-resume-smoke.yaml"
    ) in completed.stdout


@needs_make
def test_the_target_without_run_fails_with_its_usage() -> None:
    completed = _make("bench-vm-compare")

    assert completed.returncode != 0
    assert "make bench-vm-compare RUN=runs/<name>.yaml" in completed.stderr
