"""The ask_your_docs.ui: config block (activity panel, PROPOSAL §5, TDD 9).

Core-suite tests — pydantic only, no [harness-ask-your-docs] extra needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import (
    ActivityUiConfig,
    AskYourDocsUiConfig,
    ReasoningUiConfig,
)

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): no PYDOCS_* env, no cwd config."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def test_documented_defaults() -> None:
    ui = AppConfig.load().ask_your_docs.ui
    assert (ui.activity.enabled, ui.activity.live, ui.activity.technical_details) == (
        True,
        True,
        False,
    )
    assert ui.activity.collapse_when_done is True
    assert (ui.activity.result_preview_chars, ui.activity.args_max_chars) == (600, 2000)
    assert (ui.activity.max_steps_shown, ui.activity.history_keep) == (40, 20)
    assert ui.activity.editor_link is None
    assert (ui.reasoning.capture, ui.reasoning.display, ui.reasoning.max_chars) == (
        True,
        "collapsed",
        20_000,
    )
    assert ui.reasoning.think_tags is False and ui.reasoning.availability is None


def test_yaml_block_matches_the_pydantic_defaults() -> None:
    """No YAML↔Field drift: the shipped block loads equal to the model defaults."""
    assert AppConfig.load().ask_your_docs.ui == AskYourDocsUiConfig()


def test_default_yaml_ships_the_ui_keys() -> None:
    """Deleting the ui: block from the shipped YAML must fail (defaults would mask it)."""
    shipped = yaml.safe_load(
        (_ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    ui = shipped["ask_your_docs"]["ui"]
    assert ui["activity"]["result_preview_chars"] == 600
    assert ui["activity"]["editor_link"] is None
    assert ui["reasoning"]["display"] == "collapsed"
    assert ui["reasoning"]["think_tags"] is False  # a bool, never the YAML-1.1 trap "off"


def test_overlay_and_env_override(tmp_path, monkeypatch) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "ask_your_docs:\n  ui:\n    activity:\n      enabled: false\n"
        "    reasoning:\n      display: hidden\n",
        encoding="utf-8",
    )
    ui = AppConfig.load(explicit_path=overlay).ask_your_docs.ui
    assert ui.activity.enabled is False and ui.reasoning.display == "hidden"
    assert ui.activity.live is True  # untouched siblings keep defaults
    monkeypatch.setenv("PYDOCS_ASK_YOUR_DOCS__UI__ACTIVITY__TECHNICAL_DETAILS", "true")
    assert AppConfig.load().ask_your_docs.ui.activity.technical_details is True


@pytest.mark.parametrize("model", [AskYourDocsUiConfig, ActivityUiConfig, ReasoningUiConfig])
def test_unknown_keys_are_rejected_at_every_level(model) -> None:
    with pytest.raises(ValidationError):
        model(colapse_when_done=True)  # typo'd key


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("result_preview_chars", -1),
        ("result_preview_chars", 5001),
        ("args_max_chars", 39),
        ("args_max_chars", 20_001),
        ("max_steps_shown", 0),
        ("history_keep", -1),
    ],
)
def test_activity_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        ActivityUiConfig(**{field: value})


def test_activity_bound_edges_are_accepted() -> None:
    edges = ActivityUiConfig(result_preview_chars=0, args_max_chars=40, history_keep=0)
    assert edges.result_preview_chars == 0 and edges.history_keep == 0


def test_reasoning_display_and_bounds() -> None:
    with pytest.raises(ValidationError):
        ReasoningUiConfig(display="shown")
    with pytest.raises(ValidationError):
        ReasoningUiConfig(max_chars=199)
    assert ReasoningUiConfig(availability=False).availability is False


def test_editor_link_placeholders() -> None:
    link = "vscode://file/{root}/{path}:{start_line}"
    assert ActivityUiConfig(editor_link=link).editor_link == link
    with pytest.raises(ValidationError, match=r"\{line\}"):
        ActivityUiConfig(editor_link="vscode://file/{path}:{line}")
    with pytest.raises(ValidationError, match="path"):
        ActivityUiConfig(editor_link="vscode://file/{root}")
