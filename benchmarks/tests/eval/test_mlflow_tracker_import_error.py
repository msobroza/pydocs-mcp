"""Pin the optional-extra boundary: constructing MlflowExperimentTracker
without mlflow installed must raise ImportError carrying the exact uv
install command so users can copy-paste it (spec §4.5)."""

from __future__ import annotations

import sys

import pytest
from pydocs_eval.serialization import tracker_registry
from pydocs_eval.trackers import MlflowExperimentTracker

_EXPECTED_INSTALL_CMD = "uv pip install -e benchmarks[mlflow]"


def _force_mlflow_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: setting sys.modules["mlflow"] = None makes ``import mlflow``
    # raise ImportError even if mlflow is installed in the test env —
    # this is the standard CPython sentinel for blocking imports.
    monkeypatch.setitem(sys.modules, "mlflow", None)


def test_construction_without_mlflow_raises_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_mlflow_missing(monkeypatch)
    with pytest.raises(ImportError) as excinfo:
        MlflowExperimentTracker()
    assert _EXPECTED_INSTALL_CMD in str(excinfo.value)


def test_registry_build_without_mlflow_raises_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: the runner calls tracker_registry.build("mlflow") — the same
    # install error must surface there, not only from direct construction.
    _force_mlflow_missing(monkeypatch)
    with pytest.raises(ImportError) as excinfo:
        tracker_registry.build("mlflow")
    assert _EXPECTED_INSTALL_CMD in str(excinfo.value)


def test_mlflow_tracker_registered_under_name_mlflow() -> None:
    # WHY: name is the registry key. Verifying through ``names()`` keeps
    # the test honest even when the optional dep is absent — the registry
    # registration happens at import time, before any mlflow import.
    assert "mlflow" in tracker_registry.names()
