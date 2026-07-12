"""The ask_your_docs: config block (spec 2026-07-11-multimodal-image-agent §3.5).

Core-suite tests — pydantic only, no [ask-your-docs] extra needed (AC23/AC24).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig


def test_ask_your_docs_defaults_present() -> None:
    """AC23: AppConfig.load() with no overlay yields the documented defaults."""
    cfg = AppConfig.load().ask_your_docs
    assert cfg.architecture == "auto"
    assert cfg.multimodal.preferred_architecture == "vision_subagent"
    assert cfg.multimodal.detection.override is None
    assert cfg.multimodal.detection.static_table is True
    assert cfg.multimodal.detection.endpoint_probe is False
    assert cfg.multimodal.detection.image_probe is False
    assert cfg.multimodal.text_only_fallback == "reject"
    assert cfg.images.max_per_turn == 3
    assert cfg.images.max_bytes == 5_000_000


def test_ask_your_docs_yaml_overlay_overrides(tmp_path) -> None:
    """AC23: a YAML overlay overrides the shipped defaults."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n"
        "  architecture: inline\n"
        "  multimodal:\n"
        "    text_only_fallback: describe\n"
        "  images:\n"
        "    max_per_turn: 5\n",
        encoding="utf-8",
    )
    cfg = AppConfig.load(explicit_path=overlay).ask_your_docs
    assert cfg.architecture == "inline"
    assert cfg.multimodal.text_only_fallback == "describe"
    assert cfg.images.max_per_turn == 5
    # Untouched siblings keep defaults.
    assert cfg.multimodal.preferred_architecture == "vision_subagent"


def test_ask_your_docs_env_override(monkeypatch) -> None:
    """AC23: PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE works with zero new plumbing
    (env_prefix + env_nested_delimiter, app_config.py model_config)."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__ARCHITECTURE", "text_react")
    assert AppConfig.load().ask_your_docs.architecture == "text_react"


def test_ask_your_docs_yaml_matches_pydantic_defaults() -> None:
    """AC24: the defaults/default_config.yaml block round-trips equal to the
    pydantic Field defaults — no YAML↔Field drift."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    assert AppConfig.load().ask_your_docs == AskYourDocsConfig()


def test_ask_your_docs_rejects_unknown_keys() -> None:
    """Sub-model convention: extra='forbid' catches overlay typos loudly."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

    with pytest.raises(ValidationError):
        AskYourDocsConfig(architecure="auto")  # typo'd key


def test_images_config_bounds() -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ImagesConfig

    with pytest.raises(ValidationError):
        ImagesConfig(max_per_turn=0)
    with pytest.raises(ValidationError):
        ImagesConfig(max_per_turn=11)
    with pytest.raises(ValidationError):
        ImagesConfig(max_bytes=0)


def test_text_only_fallback_literal() -> None:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalConfig

    with pytest.raises(ValidationError):
        MultimodalConfig(text_only_fallback="ignore")


def test_images_session_retention_default_and_bounds() -> None:
    """Reinspect extension: the session image store keeps the last N attached
    images for the reinspect_images tool; 0 disables retention."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ImagesConfig

    assert ImagesConfig().session_retention == 12
    assert ImagesConfig(session_retention=0).session_retention == 0
    with pytest.raises(ValidationError):
        ImagesConfig(session_retention=51)


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): AppConfig.load() probes env/cwd/XDG for
    user configs — a dev machine's ambient PYDOCS_* vars or pydocs-mcp.yaml
    must not leak into these assertions."""
    import os

    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def test_default_yaml_ships_the_block_keys() -> None:
    """AC24 presence half: deleting the ask_your_docs: block from the shipped
    YAML must fail this test (defaults filling in would mask the deletion)."""
    from pathlib import Path as _P

    import yaml

    root = _P(__file__).resolve().parents[1]
    shipped = yaml.safe_load(
        (root / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    block = shipped["ask_your_docs"]
    assert block["architecture"] == "auto"
    assert block["multimodal"]["detection"]["static_table"] is True
    assert block["images"]["session_retention"] == 12


def test_images_max_reinspect_per_turn_default_and_bounds() -> None:
    """Necessity gating: the reinspect tool's per-turn vision-call budget."""
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ImagesConfig

    assert ImagesConfig().max_reinspect_per_turn == 2
    assert ImagesConfig(max_reinspect_per_turn=0).max_reinspect_per_turn == 0
    with pytest.raises(ValidationError):
        ImagesConfig(max_reinspect_per_turn=11)
