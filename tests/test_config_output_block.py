"""The output: config block — envelope + next-step pointer toggles (spec §D4/§D5)."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import pydocs_mcp
from pydocs_mcp.retrieval.config import AppConfig, SuggestionsConfig

_SHIPPED_DEFAULT_CONFIG = Path(pydocs_mcp.__file__).parent / "defaults" / "default_config.yaml"


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


def test_output_suggestions_defaults_present() -> None:
    # ADR 0007: all three deterministic-suggestion rules default ON.
    config = AppConfig.load()
    assert config.output.suggestions.grep_zero_hit is True
    assert config.output.suggestions.grep_truncated is True
    assert config.output.suggestions.search_zero_hit is True


def test_output_suggestions_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "output:\n"
        "  suggestions:\n"
        "    grep_zero_hit: false\n"
        "    grep_truncated: false\n"
        "    search_zero_hit: false\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.suggestions.grep_zero_hit is False
    assert config.output.suggestions.grep_truncated is False
    assert config.output.suggestions.search_zero_hit is False


def test_output_suggestions_checkout_rule_defaults_on_and_turns_off(tmp_path) -> None:
    # #311: the "checked-out branch is not indexed" rule is a fourth ADR 0007
    # rule, so with every flag off meta.suggestion stays null (contract §2.3).
    assert AppConfig.load().output.suggestions.checkout_not_indexed is True
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  suggestions:\n    checkout_not_indexed: false\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.suggestions.checkout_not_indexed is False


def test_output_suggestions_behind_upstream_rule_defaults_on_and_turns_off(tmp_path) -> None:
    # #318: the behind-upstream hint is a fifth ADR 0007 rule — on top of its
    # layer switch git.remote.behind_hint — so with every flag off
    # meta.suggestion stays null (contract §2.3), as #311's rule had to be.
    assert AppConfig.load().output.suggestions.behind_upstream is True
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  suggestions:\n    behind_upstream: false\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.suggestions.behind_upstream is False


def test_output_suggestions_api_zero_hit_fallback_model_default_is_on() -> None:
    # ADR 0027: the field default is the single source of the default.
    assert SuggestionsConfig().api_zero_hit_fallback_any is True


def test_output_suggestions_api_zero_hit_fallback_shipped_yaml_row_is_on() -> None:
    # The shipped default config restates the key (the YAML exemption), so a
    # missing row would only be caught by reading the YAML itself.
    shipped = yaml.safe_load(_SHIPPED_DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert shipped["output"]["suggestions"]["api_zero_hit_fallback_any"] is True
    assert AppConfig.load().output.suggestions.api_zero_hit_fallback_any is True


def test_output_suggestions_api_zero_hit_fallback_env_override_turns_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYDOCS_OUTPUT__SUGGESTIONS__API_ZERO_HIT_FALLBACK_ANY", "false")
    assert AppConfig.load().output.suggestions.api_zero_hit_fallback_any is False


def test_output_suggestions_api_zero_hit_fallback_overlay_turns_it_off(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  suggestions:\n    api_zero_hit_fallback_any: false\n")
    config = AppConfig.load(explicit_path=overlay)
    assert config.output.suggestions.api_zero_hit_fallback_any is False
    # The other rules keep their shipped defaults (the key is independent).
    assert config.output.suggestions.api_zero_hit_retry_any is True


def test_output_suggestions_unknown_key_still_rejected_beside_the_fallback_flag(
    tmp_path,
) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "output:\n  suggestions:\n    api_zero_hit_fallback_any: true\n"
        "    api_zero_hit_fallback: true\n"
    )
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


def test_output_suggestions_typo_key_rejected(tmp_path) -> None:
    """Per-rule ablation flags must fail loud on a misspelled key."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("output:\n  suggestions: { grep_zero_hits: false }\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


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
