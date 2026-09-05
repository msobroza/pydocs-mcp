"""The files: config block — filesystem-tool bounds (tool-contracts.md §3.7-3.9)."""

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, FilesConfig


def test_files_defaults_present() -> None:
    config = AppConfig.load()
    assert config.files.grep_head_limit == 100
    assert config.files.glob_head_limit == 100
    assert config.files.read_limit == 2000
    assert config.files.max_head_limit == 10000


def test_files_overridable_via_overlay(tmp_path) -> None:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "files:\n"
        "  grep_head_limit: 5\n"
        "  glob_head_limit: 7\n"
        "  read_limit: 50\n"
        "  max_head_limit: 500\n"
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.files.grep_head_limit == 5
    assert config.files.glob_head_limit == 7
    assert config.files.read_limit == 50
    assert config.files.max_head_limit == 500


def test_files_typo_key_rejected(tmp_path) -> None:
    """extra="forbid" parity with every sibling config sub-model."""
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("files:\n  grep_head_limt: 5\n")
    with pytest.raises(ValidationError):
        AppConfig.load(explicit_path=overlay)


@pytest.mark.parametrize("field", ["grep_head_limit", "glob_head_limit", "read_limit"])
def test_files_default_above_ceiling_rejected(field: str) -> None:
    """A YAML default above max_head_limit would always exceed the client-cap
    ceiling — reject at load time, mirroring SearchOutputConfig._default_le_max."""
    with pytest.raises(ValidationError):
        FilesConfig(**{field: 20000})


@pytest.mark.parametrize(
    "field", ["grep_head_limit", "glob_head_limit", "read_limit", "max_head_limit"]
)
def test_files_nonpositive_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        FilesConfig(**{field: 0})
