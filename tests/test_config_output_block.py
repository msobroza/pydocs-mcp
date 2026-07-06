"""The output: config block — envelope + next-step pointer toggles (spec §D4/§D5)."""

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
