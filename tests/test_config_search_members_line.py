"""``search.output.members_line`` — the Members-line experiment's knob (issue #393).

The knob ships off; the experiment's arm turns it on by YAML overlay. Pydantic
only, no index needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from pydocs_mcp.retrieval.config import AppConfig

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hermeticity (repo convention): no PYDOCS_* env, no cwd config."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def test_the_members_line_is_off_by_default() -> None:
    assert AppConfig.load().search.output.members_line is False


def test_the_shipped_yaml_restates_the_knob() -> None:
    """Deleting the key from the shipped YAML must fail here, not be masked by the Field default."""
    shipped = yaml.safe_load(
        (_ROOT / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    )
    assert shipped["search"]["output"]["members_line"] is False


def test_a_yaml_overlay_turns_the_members_line_on(tmp_path: Path) -> None:
    """The experiment arm's mechanism: one overlay key, every other default kept."""
    path = tmp_path / "overlay.yaml"
    path.write_text("search:\n  output:\n    members_line: true\n", encoding="utf-8")
    output = AppConfig.load(explicit_path=path).search.output
    assert output.members_line is True
    assert output.budget_tokens == AppConfig.load().search.output.budget_tokens


def test_the_knob_has_an_env_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_SEARCH__OUTPUT__MEMBERS_LINE", "true")
    assert AppConfig.load().search.output.members_line is True
