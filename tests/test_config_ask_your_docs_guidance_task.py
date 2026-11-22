"""The ``ask_your_docs.guidance_task`` knob — the config seam (issue #391, spec
2026-11-22-guidance-task-experiment-design "The knob").

Core-suite tests — pydantic only, no [harness-ask-your-docs] extra needed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, GuidanceTask

_ACCEPTED_VALUES = ("none", "repo_qa", "vuln", "bug_loc")


def _shipped_ask_your_docs_block() -> dict[str, Any]:
    """The shipped defaults YAML's ``ask_your_docs`` block, read directly so that
    deleting the key fails loudly instead of being masked by the Field default."""
    root = Path(__file__).resolve().parents[1]
    text = (root / "python/pydocs_mcp/defaults/default_config.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["ask_your_docs"]


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): clears PYDOCS_* env vars and chdirs away
    from any cwd config."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def test_the_vocabulary_is_four_members_with_their_spelled_values() -> None:
    assert [(member.name, member.value) for member in GuidanceTask] == [
        ("NONE", "none"),
        ("REPO_QA", "repo_qa"),
        ("VULN", "vuln"),
        ("BUG_LOC", "bug_loc"),
    ]


def test_the_task_members_equal_the_loaders_task_names_in_order() -> None:
    """Parity: a widening of TASK_NAMES must widen the knob in the same edit."""
    from pydocs_mcp.harness.core.skill_artifact_loader import TASK_NAMES

    tasks = tuple(member.value for member in GuidanceTask if member is not GuidanceTask.NONE)
    assert tasks == TASK_NAMES


def test_the_default_is_none_on_the_typed_model() -> None:
    assert AskYourDocsConfig().guidance_task is GuidanceTask.NONE


def test_the_shipped_yaml_restates_the_model_default() -> None:
    block = _shipped_ask_your_docs_block()
    assert block["guidance_task"] == AskYourDocsConfig().guidance_task.value
    assert AppConfig.load().ask_your_docs.guidance_task is GuidanceTask.NONE


@pytest.mark.parametrize("value", [v for v in _ACCEPTED_VALUES if v != "none"])
def test_every_task_value_is_accepted(value: str) -> None:
    assert AskYourDocsConfig(guidance_task=value).guidance_task == value


def test_a_mistyped_value_is_refused_naming_it_and_the_accepted_set() -> None:
    with pytest.raises(ValidationError) as excinfo:
        AskYourDocsConfig.model_validate({"guidance_task": "repo_qq"})
    message = str(excinfo.value)
    assert "repo_qq" in message
    assert all(f"'{value}'" in message for value in _ACCEPTED_VALUES)


def test_a_mistyped_value_in_a_yaml_overlay_is_refused_at_load(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("ask_your_docs:\n  guidance_task: chat\n", encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)
    assert "chat" in str(excinfo.value) and "'repo_qa'" in str(excinfo.value)


def test_importing_the_config_pulls_in_no_harness_module() -> None:
    probe = (
        "import sys\n"
        "from pydocs_mcp.retrieval.config.ask_your_docs_models import GuidanceTask\n"
        "import pydocs_mcp.retrieval.config\n"
        "print(sorted(m for m in sys.modules if m.startswith('pydocs_mcp.harness')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "[]"
