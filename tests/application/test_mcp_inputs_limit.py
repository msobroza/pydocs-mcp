"""Tests for ``LookupInput.limit`` / ``SearchInput.limit`` +
``configure_from_app_config`` (sub-PR #5c + post-trilogy polish).

The wire under test:

* ``configure_from_app_config(cfg)`` is called ONCE at server / CLI startup.
* It updates these module-level slots:
    1. ``mcp_inputs._LIMIT_DEFAULT`` / ``mcp_inputs._LIMIT_MAX`` — read by
       ``LookupInput.limit`` (via ``default_factory`` + ``field_validator``).
    2. ``mcp_inputs._SEARCH_LIMIT_DEFAULT`` /
       ``mcp_inputs._SEARCH_LIMIT_MAX`` — read by ``SearchInput.limit``
       (parity with LookupInput).
    3. ``extraction.pipeline.stages._CAPTURE_CONFIG`` — read by
       ``ReferenceCaptureStage`` at runtime.

The Pydantic model stays stateless from a test's POV: every fresh
``LookupInput()`` / ``SearchInput()`` re-reads the module slots at
validation time, so flipping the config and instantiating again is all
the harness needs to do.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import (
    LookupInput,
    SearchInput,
    configure_from_app_config,
)
from pydocs_mcp.extraction.pipeline import stages as pipeline_stages
from pydocs_mcp.retrieval.config import (
    ReferenceCaptureConfig,
    ReferenceGraphConfig,
    ReferenceOutputConfig,
    SearchConfig,
    SearchOutputConfig,
)


class _StubCfg:
    """Minimal stand-in for ``AppConfig`` — only the slice we read."""

    def __init__(
        self,
        reference_graph: ReferenceGraphConfig,
        search: SearchConfig | None = None,
    ) -> None:
        self.reference_graph = reference_graph
        # Default SearchConfig matches the shipped YAML so tests that
        # only exercise the LookupInput slots don't need to spell out
        # search explicitly.
        self.search = search if search is not None else SearchConfig()


@pytest.fixture(autouse=True)
def _restore_module_state() -> None:
    """Snapshot + restore module-level state so tests don't leak state."""
    saved_default = mcp_inputs._LIMIT_DEFAULT
    saved_max = mcp_inputs._LIMIT_MAX
    saved_search_default = mcp_inputs._SEARCH_LIMIT_DEFAULT
    saved_search_max = mcp_inputs._SEARCH_LIMIT_MAX
    saved_capture = pipeline_stages._CAPTURE_CONFIG
    try:
        yield
    finally:
        mcp_inputs._LIMIT_DEFAULT = saved_default
        mcp_inputs._LIMIT_MAX = saved_max
        mcp_inputs._SEARCH_LIMIT_DEFAULT = saved_search_default
        mcp_inputs._SEARCH_LIMIT_MAX = saved_search_max
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


# ─── SearchInput: parity coverage with LookupInput ──────────────────────


def test_search_input_limit_default_reads_from_configure() -> None:
    """After ``configure_from_app_config``, a fresh ``SearchInput`` picks
    up the new default from ``search.output.default_limit``."""
    cfg = _StubCfg(
        ReferenceGraphConfig(),
        search=SearchConfig(
            output=SearchOutputConfig(default_limit=7, max_limit=42),
        ),
    )
    configure_from_app_config(cfg)
    assert SearchInput(query="x").limit == 7


def test_search_input_limit_explicit_value_overrides_default() -> None:
    """A client-supplied ``limit=`` overrides the configured default —
    proves the default_factory only fires when ``limit`` is absent."""
    cfg = _StubCfg(
        ReferenceGraphConfig(),
        search=SearchConfig(
            output=SearchOutputConfig(default_limit=7, max_limit=500),
        ),
    )
    configure_from_app_config(cfg)
    assert SearchInput(query="x", limit=100).limit == 100


def test_search_input_limit_max_validator_reads_from_configure() -> None:
    """After ``configure_from_app_config`` raises the ceiling, values up
    to the new max pass and values above fail — proves the
    ``field_validator`` reads ``_SEARCH_LIMIT_MAX`` at runtime."""
    cfg = _StubCfg(
        ReferenceGraphConfig(),
        search=SearchConfig(
            output=SearchOutputConfig(default_limit=10, max_limit=50),
        ),
    )
    configure_from_app_config(cfg)
    # At the new ceiling — accepted.
    SearchInput(query="x", limit=50)
    # Above the new ceiling — rejected.
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=51)


def test_search_input_limit_default_uses_constant_before_configure() -> None:
    """Before any call to ``configure_from_app_config``, ``SearchInput()``
    falls back to the module-level ``_SEARCH_LIMIT_DEFAULT`` constant (10)."""
    mcp_inputs._SEARCH_LIMIT_DEFAULT = 10
    mcp_inputs._SEARCH_LIMIT_MAX = 1000
    assert SearchInput(query="x").limit == 10
    SearchInput(query="x", limit=1000)
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=1001)
