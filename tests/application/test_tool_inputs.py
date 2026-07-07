"""Input models for the six task-shaped tools (spec §D1)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SymbolInput,
    WhyInput,
    configure_from_app_config,
)


def test_symbol_input_defaults_and_depth_enum() -> None:
    payload = SymbolInput(target="pkg.mod.X")
    assert (payload.depth, payload.project) == ("summary", "")
    assert SymbolInput(target="t", depth="source").depth == "source"
    with pytest.raises(ValidationError):
        SymbolInput(target="t", depth="full")
    with pytest.raises(ValidationError):
        SymbolInput(target="")  # empty target is search_codebase's job


def test_context_input_targets_bounds() -> None:
    assert ContextInput(targets=["a"]).targets == ["a"]
    with pytest.raises(ValidationError):
        ContextInput(targets=[])  # spec §D1: empty list = validation error
    with pytest.raises(ValidationError):
        ContextInput(targets=["x"] * 21)  # max 20


def test_references_input_direction_enum_and_limit() -> None:
    payload = ReferencesInput(target="pkg.mod.f")
    assert payload.direction == "callers"
    assert payload.limit >= 1  # YAML-wired default, not a literal here
    for direction in ("callers", "callees", "inherits", "impact", "governed_by"):
        assert ReferencesInput(target="t", direction=direction).direction == direction
    with pytest.raises(ValidationError):
        ReferencesInput(target="t", direction="uses")


def test_why_input_shapes() -> None:
    assert WhyInput().query == "" and WhyInput().targets is None
    assert WhyInput(query="auth").query == "auth"
    assert WhyInput(targets=["a", "b"]).targets == ["a", "b"]
    with pytest.raises(ValidationError):
        WhyInput(targets=[])  # empty list is an error, not dashboard mode


def test_overview_input() -> None:
    assert OverviewInput().package == ""
    assert OverviewInput(package="fastapi", project="backend").project == "backend"


# ─── symbol_source config wiring (Task 7) ────────────────────────────────
#
# Mirrors the LookupInput/SearchInput limit-wiring tests in
# tests/application/test_mcp_inputs_limit.py: flip the YAML-loaded value via
# ``configure_from_app_config`` and assert the effect lands (here, the
# get_symbol(depth="source") line cap threaded into ``SymbolSourceService``).


@pytest.fixture
def _restore_symbol_source_slot():
    """Restore the module-level symbol-source slot so the round-trip test
    can flip it without leaking state into sibling tests."""
    saved = mcp_inputs._SYMBOL_SOURCE_MAX_LINES
    try:
        yield
    finally:
        mcp_inputs._SYMBOL_SOURCE_MAX_LINES = saved


def test_configure_from_app_config_installs_symbol_source_max_lines(
    _restore_symbol_source_slot,
) -> None:
    """``configure_from_app_config`` pushes ``cfg.symbol_source.max_lines``
    into the module-level slot — proves YAML flows into the get_symbol
    line cap (parity with the LookupInput/SearchInput limit slots)."""
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.config.models import SymbolSourceConfig

    cfg = AppConfig(symbol_source=SymbolSourceConfig(max_lines=123))
    configure_from_app_config(cfg)
    assert mcp_inputs._SYMBOL_SOURCE_MAX_LINES == 123


def test_symbol_source_factory_reads_max_lines_from_config(
    _restore_symbol_source_slot,
) -> None:
    """The per-project ``SymbolSourceService`` builder threads
    ``cfg.symbol_source.max_lines`` into the service — the config→service
    wire the plan defers to Task 7."""
    from pathlib import Path

    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.config.models import SymbolSourceConfig
    from pydocs_mcp.storage.factories import build_sqlite_symbol_source_service

    cfg = AppConfig(symbol_source=SymbolSourceConfig(max_lines=77))
    svc = build_sqlite_symbol_source_service(Path("/nonexistent.db"), config=cfg)
    assert svc.max_lines == 77
