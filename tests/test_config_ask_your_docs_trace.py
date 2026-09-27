"""The ask_your_docs.trace: config block — the chat page's opt-in trace persistence.

Core-suite tests — pydantic only, no [harness-ask-your-docs] extra needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_trace_models import ChatTraceConfig

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): no PYDOCS_* env, no cwd config."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def _overlay(tmp_path: Path, trace_block: str) -> AppConfig:
    path = tmp_path / "overlay.yaml"
    path.write_text("ask_your_docs:\n  trace:\n" + trace_block, encoding="utf-8")
    return AppConfig.load(explicit_path=path)


def test_the_trace_is_off_by_default() -> None:
    trace = AppConfig.load().ask_your_docs.trace
    assert trace.enabled is False
    assert trace.dir == ""


def test_the_shipped_yaml_restates_the_block() -> None:
    """Deleting the block from the shipped YAML must fail here, not be masked by Field defaults."""
    shipped = yaml.safe_load(
        (_ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    assert shipped["ask_your_docs"]["trace"] == {"enabled": False, "dir": ""}


def test_a_yaml_overlay_turns_the_trace_on(tmp_path: Path) -> None:
    trace = _overlay(tmp_path, "    enabled: true\n    dir: ~/chat-traces\n").ask_your_docs.trace
    assert trace.enabled is True
    assert trace.dir == "~/chat-traces"  # kept as written; the page expands it


def test_the_knob_has_its_own_env_spelling(monkeypatch) -> None:
    """``PYDOCS_ASK_YOUR_DOCS__TRACE__ENABLED`` is the knob; ``PYDOCS_TRACE__*`` is not."""
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__TRACE__ENABLED", "true")
    assert AppConfig.load().ask_your_docs.trace.enabled is True


@pytest.mark.parametrize("value", ["maybe", "2", "[true]"])
def test_a_non_bool_enabled_is_rejected_with_the_value_and_the_shape(
    tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        _overlay(tmp_path, f"    enabled: {value}\n")
    message = str(caught.value)
    assert "ask_your_docs.trace.enabled" in message
    assert "valid boolean" in message
    assert f"input_value={yaml.safe_load(value)!r}" in message


def test_the_block_forbids_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="enable"):
        _overlay(tmp_path, "    enable: true\n")  # typo'd key
    with pytest.raises(ValidationError):
        ChatTraceConfig(directory="/t")
