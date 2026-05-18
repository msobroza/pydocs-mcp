"""Tests for ``LookupInput.limit`` + ``configure_from_app_config`` (sub-PR #5c).

The wire under test:

* ``configure_from_app_config(cfg)`` is called ONCE at server / CLI startup.
* It updates two module-level slots:
    1. ``mcp_inputs._LIMIT_DEFAULT`` / ``mcp_inputs._LIMIT_MAX`` — read by
       ``LookupInput.limit`` (via ``default_factory`` + ``field_validator``).
    2. ``extraction.pipeline.stages._CAPTURE_CONFIG`` — read by
       ``ReferenceCaptureStage`` at runtime.

The Pydantic model stays stateless from a test's POV: every fresh
``LookupInput()`` re-reads the module slots at validation time, so flipping
the config and instantiating again is all the harness needs to do.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import (
    LookupInput,
    configure_from_app_config,
)
from pydocs_mcp.extraction.pipeline import stages as pipeline_stages
from pydocs_mcp.retrieval.config import (
    ReferenceCaptureConfig,
    ReferenceGraphConfig,
    ReferenceOutputConfig,
)


class _StubCfg:
    """Minimal stand-in for ``AppConfig`` — only the slice we read."""

    def __init__(self, reference_graph: ReferenceGraphConfig) -> None:
        self.reference_graph = reference_graph


@pytest.fixture(autouse=True)
def _restore_module_state() -> None:
    """Snapshot + restore module-level state so tests don't leak state."""
    saved_default = mcp_inputs._LIMIT_DEFAULT
    saved_max = mcp_inputs._LIMIT_MAX
    saved_capture = pipeline_stages._CAPTURE_CONFIG
    try:
        yield
    finally:
        mcp_inputs._LIMIT_DEFAULT = saved_default
        mcp_inputs._LIMIT_MAX = saved_max
        pipeline_stages._CAPTURE_CONFIG = saved_capture


def test_lookup_input_limit_default_reads_from_configure() -> None:
    """After ``configure_from_app_config``, a fresh ``LookupInput`` picks
    up the new default from YAML — proves the field is dynamic, not frozen
    at class-def time."""
    cfg = _StubCfg(
        ReferenceGraphConfig(
            capture=ReferenceCaptureConfig(),
            output=ReferenceOutputConfig(default_limit=25, max_limit=500),
        ),
    )
    configure_from_app_config(cfg)
    assert LookupInput().limit == 25


def test_lookup_input_limit_explicit_value_overrides_default() -> None:
    """A client-supplied ``limit=`` overrides the configured default —
    proves the default_factory only fires when ``limit`` is absent."""
    cfg = _StubCfg(
        ReferenceGraphConfig(
            output=ReferenceOutputConfig(default_limit=25, max_limit=500),
        ),
    )
    configure_from_app_config(cfg)
    assert LookupInput(limit=100).limit == 100


def test_lookup_input_limit_ge_one_enforced() -> None:
    """``limit`` must be >= 1 — Pydantic-level constraint, independent of
    YAML config."""
    with pytest.raises(ValidationError):
        LookupInput(limit=0)
    with pytest.raises(ValidationError):
        LookupInput(limit=-5)


def test_lookup_input_limit_max_validator_reads_from_configure() -> None:
    """After ``configure_from_app_config`` raises the ceiling, values up
    to the new max pass and values above fail — proves the
    ``field_validator`` reads ``_LIMIT_MAX`` at runtime, not class-def."""
    cfg = _StubCfg(
        ReferenceGraphConfig(
            output=ReferenceOutputConfig(default_limit=50, max_limit=200),
        ),
    )
    configure_from_app_config(cfg)
    # At the new ceiling — accepted.
    LookupInput(limit=200)
    # Above the new ceiling — rejected.
    with pytest.raises(ValidationError):
        LookupInput(limit=201)


def test_lookup_input_limit_default_uses_constant_before_configure() -> None:
    """Before any call to ``configure_from_app_config``, ``LookupInput()``
    falls back to the module-level ``_LIMIT_DEFAULT`` constant (50)."""
    # Reset the module slots to their initial values, simulating "server
    # never called configure_from_app_config". The autouse fixture will
    # restore whatever was set before.
    mcp_inputs._LIMIT_DEFAULT = 50
    mcp_inputs._LIMIT_MAX = 1000
    assert LookupInput().limit == 50
    # The ceiling is also at the constant default.
    LookupInput(limit=1000)
    with pytest.raises(ValidationError):
        LookupInput(limit=1001)
