"""Tests for ``AppConfig.reference_graph`` typed sub-model (sub-PR #5c, AC #4).

Pins the YAML-driven defaults shipped in ``defaults/default_config.yaml``,
the cross-field ``default_limit <= max_limit`` validator, the YAML overlay
behaviour for ``kinds``, and the ``Literal`` rejection of unknown kinds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import (
    AppConfig,
    ReferenceCaptureConfig,
    ReferenceGraphConfig,
    ReferenceOutputConfig,
)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def test_reference_graph_defaults_present_after_load():
    """Shipped baseline yields ``reference_graph`` populated with the 5 keys.

    Pins the AC #4 shape: capture.{enabled, kinds} + output.{default_limit, max_limit}
    with the values declared in ``defaults/default_config.yaml``. MENTIONS is
    opt-in — it must NOT appear in the default ``kinds`` list.
    """
    config = AppConfig.load()
    rg = config.reference_graph
    assert isinstance(rg, ReferenceGraphConfig)
    assert rg.capture.enabled is True
    assert list(rg.capture.kinds) == ["calls", "imports", "inherits"]
    assert "mentions" not in rg.capture.kinds  # opt-in only
    assert rg.output.default_limit == 50
    assert rg.output.max_limit == 1000


def test_reference_graph_output_default_le_max_validator():
    """``default_limit > max_limit`` is rejected by the cross-field validator.

    Guards against silent misconfiguration where the YAML accidentally
    inverts the bounds — the resulting LookupInput default would always
    fail the max validator. Better to fail loud at config-load time.
    """
    with pytest.raises(ValidationError) as excinfo:
        ReferenceOutputConfig(default_limit=200, max_limit=50)
    msg = str(excinfo.value)
    assert "default_limit" in msg
    assert "max_limit" in msg


def test_reference_graph_yaml_overlay_parses_kinds_list(tmp_path):
    """User YAML overlay can opt into MENTIONS by listing it under ``kinds``."""
    user_file = tmp_path / "pydocs-mcp.yaml"
    user_file.write_text(
        "reference_graph:\n"
        "  capture:\n"
        "    kinds: [calls, imports, inherits, mentions]\n"
        "  output:\n"
        "    default_limit: 25\n"
        "    max_limit: 500\n"
    )
    config = AppConfig.load(explicit_path=user_file)
    rg = config.reference_graph
    assert list(rg.capture.kinds) == ["calls", "imports", "inherits", "mentions"]
    assert rg.capture.enabled is True  # untouched key keeps shipped default
    assert rg.output.default_limit == 25
    assert rg.output.max_limit == 500


def test_reference_graph_kinds_rejects_unknown_kind():
    """``Literal[...]`` typing rejects values outside the four known kinds.

    Pins the Open/Closed extension point: adding a new kind requires
    extending the ``Literal`` (and the matching ``ReferenceKind`` enum),
    not silently accepting arbitrary strings.
    """
    with pytest.raises(ValidationError):
        ReferenceCaptureConfig(kinds=["calls", "definitely_not_a_kind"])


def test_reference_graph_resolver_defaults_present_after_load():
    """Defaults shipped in default_config.yaml carry through AppConfig.load()."""
    cfg = AppConfig.load()
    rg = cfg.reference_graph
    assert rg.resolver.include_stdlib is True


def test_reference_graph_resolver_yaml_overlay_can_disable_stdlib(tmp_path):
    """YAML overlay can flip include_stdlib off (e.g., for benchmark A/B)."""
    overlay = tmp_path / "custom.yaml"
    overlay.write_text("reference_graph:\n  resolver:\n    include_stdlib: false\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.reference_graph.resolver.include_stdlib is False
    # Other defaults still hold:
    assert cfg.reference_graph.capture.enabled is True
    assert cfg.reference_graph.output.default_limit == 50


def test_reference_resolver_config_typed():
    """ReferenceResolverConfig is a typed Pydantic model with the expected field."""
    from pydocs_mcp.retrieval.config import ReferenceResolverConfig

    cfg = ReferenceResolverConfig()
    assert cfg.include_stdlib is True
    explicit = ReferenceResolverConfig(include_stdlib=False)
    assert explicit.include_stdlib is False
