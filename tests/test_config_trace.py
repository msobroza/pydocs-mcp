"""Phase 2 Task 1 — ``trace:`` AppConfig sub-model (ADR 0009 action item 1).

``trace.enabled`` / ``trace.dir`` are YAML tunables; ``trace.trajectory_id``
is env-only by documentation (``PYDOCS_TRACE__TRAJECTORY_ID``) — the typed
field must exist because ``extra="ignore"`` on ``AppConfig`` silently drops
unbacked env vars.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.config import AppConfig, TraceConfig


def test_trace_config_defaults() -> None:
    cfg = TraceConfig()
    assert cfg.enabled is False
    assert cfg.dir is None
    assert cfg.trajectory_id is None


def test_trace_config_forbids_extra_keys() -> None:
    """Typo-catching: ``enable`` (sic) must fail load, not silently drop."""
    with pytest.raises(ValueError):
        TraceConfig(enable=True)  # type: ignore[call-arg]


def test_app_config_trace_field_present() -> None:
    cfg = AppConfig.load(explicit_path=None)
    assert isinstance(cfg.trace, TraceConfig)
    assert cfg.trace.enabled is False
    assert cfg.trace.dir is None


def test_trace_trajectory_id_env_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ADR 0009 correlation channel: the `.mcp.json` env map injects
    ``PYDOCS_TRACE__*`` and the loaded config must carry the values."""
    monkeypatch.setenv("PYDOCS_TRACE__TRAJECTORY_ID", "3f6b2a1c-run")
    monkeypatch.setenv("PYDOCS_TRACE__DIR", "/tmp/traces")
    monkeypatch.setenv("PYDOCS_TRACE__ENABLED", "true")
    cfg = AppConfig.load(explicit_path=None)
    assert cfg.trace.trajectory_id == "3f6b2a1c-run"
    assert cfg.trace.dir == "/tmp/traces"
    assert cfg.trace.enabled is True
