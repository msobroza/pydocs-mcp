"""Composition swap — real ``DecisionService`` behind ``decision_capture.enabled``.

Pins the one wiring branch in ``server._build_project_services`` (Task 5): when
``config.decision_capture.enabled`` is True (the shipped default) the loaded
project's ``decisions`` is a real :class:`DecisionService`; when it is False the
Null impl stays wired so ``get_why`` keeps raising the YAML-anchored
``ServiceUnavailableError``.

If this regresses, ``get_why`` either silently degrades to the Null raise even
when capture is on, or (worse) the real service is wired against a deployment
that never mined decisions — so the swap is the exact seam worth pinning.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.decision_service import DecisionService
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.multirepo import load_project
from pydocs_mcp.retrieval.config import AppConfig, DecisionCaptureConfig
from pydocs_mcp.server import _build_project_services


def _loaded(tmp_path: Path):
    """A minimal loaded project over a freshly-schema'd empty db."""
    db_path = tmp_path / "wiring.db"
    open_index_database(db_path).close()
    return load_project(db_path)


def test_capture_enabled_wires_real_decision_service(tmp_path: Path) -> None:
    """``decision_capture.enabled=True`` (default) → real ``DecisionService``."""
    loaded = _loaded(tmp_path)
    config = AppConfig()  # decision_capture.enabled defaults to True
    assert config.decision_capture.enabled is True

    services = _build_project_services(loaded, config)

    assert isinstance(services.decisions, DecisionService)
    # The read default is threaded from YAML, never re-encoded here.
    assert services.decisions.default_limit == config.decisions.output.default_limit
    # The service composes the SAME per-project docs search the card / search
    # tools use — one semantic-ranking authority, no second pipeline.
    assert services.decisions.docs is services.docs


def test_capture_disabled_keeps_null_decision_service(tmp_path: Path) -> None:
    """``decision_capture.enabled=False`` → Null impl (still raises on use)."""
    loaded = _loaded(tmp_path)
    config = AppConfig(decision_capture=DecisionCaptureConfig(enabled=False))
    assert config.decision_capture.enabled is False

    services = _build_project_services(loaded, config)

    assert isinstance(services.decisions, NullDecisionService)
