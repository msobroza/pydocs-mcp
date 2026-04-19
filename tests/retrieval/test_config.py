"""Tests for AppConfig.load() precedence."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig


def test_appconfig_defaults_absent_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    # No explicit path, no env, no cwd file → defaults
    config = AppConfig.load()
    assert config.chunk is None
    assert config.member is None
    assert config.cache_dir == Path.home() / ".pydocs-mcp"


def test_appconfig_explicit_path_wins(tmp_path, monkeypatch):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("log_level: debug\n")
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)

    config = AppConfig.load(explicit_path=yaml_file)
    assert config.log_level == "debug"


def test_appconfig_env_var_used_when_no_explicit(tmp_path, monkeypatch):
    yaml_file = tmp_path / "env.yaml"
    yaml_file.write_text("log_level: warning\n")
    monkeypatch.setenv("PYDOCS_CONFIG_PATH", str(yaml_file))

    config = AppConfig.load()
    assert config.log_level == "warning"


def test_appconfig_cwd_local_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    yaml_file = tmp_path / "pydocs-mcp.yaml"
    yaml_file.write_text("log_level: error\n")
    monkeypatch.chdir(tmp_path)

    config = AppConfig.load()
    assert config.log_level == "error"
