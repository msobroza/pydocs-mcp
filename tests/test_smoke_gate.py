"""Regression tests for scripts/smoke_check_benchmark_imports.py.

The gate once pointed at a deleted directory and stayed vacuously green for
months. These tests pin the two properties that prevent a recurrence: the
scan over the real repo collects a non-zero number of imports, and a scan
that collects zero imports is a FAILURE, not a pass.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "smoke_check_benchmark_imports.py"
)


def _load_gate():
    spec = importlib.util.spec_from_file_location("smoke_gate", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gate_scans_real_imports_and_passes() -> None:
    gate = _load_gate()
    files = sorted({py for d in gate.BENCH_DIRS for py in d.rglob("*.py")})
    collected = sum(len(gate.collect_pydocs_imports(py)) for py in files)
    assert collected > 0, "gate scan collected zero pydocs_mcp imports — dead scan dirs?"
    assert gate.main() == 0


def test_gate_fails_when_scan_is_vacuous(tmp_path: pathlib.Path, monkeypatch) -> None:
    gate = _load_gate()
    monkeypatch.setattr(gate, "BENCH_DIRS", (tmp_path,))
    assert gate.main() == 1
