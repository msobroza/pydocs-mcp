"""Input models for the six task-shaped tools (spec §D1)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    OverviewInput,
    ReferencesInput,
    SymbolInput,
    WhyInput,
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
    for direction in ("callers", "callees", "inherits", "impact"):
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
