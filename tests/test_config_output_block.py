"""The output: config block — envelope + next-step pointer toggles (spec §D4/§D5)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig


def test_output_defaults_present() -> None:
    config = AppConfig.load()
    assert config.output.envelope.enabled is True
    assert config.output.envelope.head_check_ttl_seconds == 5.0
    assert config.output.next_pointers.enabled is True


def test_output_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "output:\n"
        "  envelope: { enabled: false, head_check_ttl_seconds: 30 }\n"
        "  next_pointers: { enabled: false }\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.envelope.enabled is False
    assert config.output.envelope.head_check_ttl_seconds == 30.0
    assert config.output.next_pointers.enabled is False


def test_output_envelope_typo_key_rejected(tmp_path) -> None:
    """A typo'd envelope key (``enabld`` for ``enabled``) must fail loud.

    Every sibling config sub-model (OverviewConfig, ImpactConfig, ...) sets
    ``extra="forbid"`` so a misspelled YAML key raises at load time instead
    of silently no-op'ing. EnvelopeConfig/NextPointersConfig/OutputConfig
    were missing that guard: a deployment that typos ``enabld: false``
    believing it disabled the freshness envelope would keep it enabled with
    no error anywhere.
    """
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  envelope: { enabld: false }\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


def test_output_next_pointers_typo_key_rejected(tmp_path) -> None:
    """Same typo-catching guard on the ``next_pointers`` sub-block."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  next_pointers: { enable: false }\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


def test_output_block_typo_key_rejected(tmp_path) -> None:
    """Same typo-catching guard on the ``output`` block itself."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  envelop: { enabled: false }\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


def test_output_config_instances_do_not_share_mutable_state() -> None:
    """``OutputConfig`` field defaults must not alias across instances.

    ``EnvelopeConfig()`` / ``NextPointersConfig()`` used as bare default
    *values* (not ``default_factory``) risk two ``AppConfig`` instances
    sharing the same nested-model object. Mutating one's ``output.envelope``
    must never leak into a freshly-loaded config.
    """
    config_a = AppConfig.load()
    config_b = AppConfig.load()
    assert config_a.output.envelope is not config_b.output.envelope
    object.__setattr__(config_a.output.envelope, "enabled", False)
    assert config_b.output.envelope.enabled is True
