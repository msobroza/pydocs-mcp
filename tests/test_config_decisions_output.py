"""The decisions.output config block — get_why output bounds (spec §D9/§D11).

Mirrors ``ReferenceOutputConfig``'s validator shape: two YAML knobs
(``default_limit`` / ``max_limit``) with ``ge=1`` field validation and a
cross-field ``default_limit <= max_limit`` check. Also pins that
``NullDecisionService`` structurally satisfies the new ``DecisionNavigator``
Protocol via runtime ``isinstance``.
"""

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.protocols import DecisionNavigator
from pydocs_mcp.retrieval.config import AppConfig, DecisionsOutputConfig


def test_decisions_output_defaults_present() -> None:
    config = AppConfig.load()
    assert config.decisions.output.default_limit == 10
    assert config.decisions.output.max_limit == 100


def test_decisions_output_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("decisions:\n  output:\n    default_limit: 5\n    max_limit: 25\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.decisions.output.default_limit == 5
    assert config.decisions.output.max_limit == 25


def test_decisions_output_ge_validation_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        DecisionsOutputConfig(default_limit=0)


def test_decisions_output_default_le_max_validator() -> None:
    """``default_limit > max_limit`` is rejected by the cross-field validator."""
    with pytest.raises(ValidationError) as excinfo:
        DecisionsOutputConfig(default_limit=200, max_limit=50)
    msg = str(excinfo.value)
    assert "default_limit" in msg
    assert "max_limit" in msg


def test_null_decision_service_satisfies_navigator_protocol() -> None:
    """runtime_checkable: the Null impl and the real service share the contract."""
    assert isinstance(NullDecisionService(), DecisionNavigator)
