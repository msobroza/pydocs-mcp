"""Tests for AppConfig YAML layering + PipelineRouteEntry validator (spec §5.9)."""
from __future__ import annotations

import importlib.resources
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, PipelineRouteEntry, _resolve_pipeline_path


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def test_appconfig_loads_shipped_defaults_absent_user_file():
    """With no user YAML and no env overrides, every value comes from the
    shipped ``presets/default_config.yaml`` baseline layer (spec §5.9, AC #14)."""
    config = AppConfig.load()
    assert config.metadata_schemas["chunk"] == ("package", "scope", "origin", "title", "module")
    assert config.metadata_schemas["member"] == ("package", "scope", "module", "name", "kind")
    assert config.log_level == "info"
    # Pipelines default to the shipped routes
    assert "chunk" in config.pipelines
    assert "member" in config.pipelines


def test_appconfig_user_yaml_overlays_shipped_baseline(tmp_path):
    """User YAML overrides selected keys; unmentioned keys keep shipped values."""
    user_file = tmp_path / "pydocs-mcp.yaml"
    user_file.write_text(
        "metadata_schemas:\n"
        "  chunk: [package, scope, origin, title, module, language]\n"
    )
    config = AppConfig.load(explicit_path=user_file)
    # Overlay replaces the chunk schema wholesale
    assert config.metadata_schemas["chunk"] == (
        "package", "scope", "origin", "title", "module", "language",
    )
    # The member schema stays at the shipped default
    assert config.metadata_schemas["member"] == ("package", "scope", "module", "name", "kind")


def test_appconfig_env_var_overrides_yaml(monkeypatch, tmp_path):
    """Env vars beat both user YAML and the shipped baseline."""
    user_file = tmp_path / "pydocs-mcp.yaml"
    user_file.write_text("log_level: warning\n")
    monkeypatch.setenv("PYDOCS_LOG_LEVEL", "debug")
    config = AppConfig.load(explicit_path=user_file)
    assert config.log_level == "debug"


def test_appconfig_explicit_path_wins_over_cwd(tmp_path, monkeypatch):
    """An explicit file beats the cwd-local pydocs-mcp.yaml."""
    cwd_file = tmp_path / "pydocs-mcp.yaml"
    cwd_file.write_text("log_level: error\n")
    explicit_file = tmp_path / "explicit.yaml"
    explicit_file.write_text("log_level: warning\n")
    monkeypatch.chdir(tmp_path)
    config = AppConfig.load(explicit_path=explicit_file)
    assert config.log_level == "warning"


def test_appconfig_env_config_path_used_when_no_explicit(tmp_path, monkeypatch):
    user_file = tmp_path / "env.yaml"
    user_file.write_text("log_level: warning\n")
    monkeypatch.setenv("PYDOCS_CONFIG_PATH", str(user_file))
    config = AppConfig.load()
    assert config.log_level == "warning"


def test_appconfig_cwd_local_file(tmp_path, monkeypatch):
    yaml_file = tmp_path / "pydocs-mcp.yaml"
    yaml_file.write_text("log_level: error\n")
    monkeypatch.chdir(tmp_path)
    config = AppConfig.load()
    assert config.log_level == "error"


# ── PipelineRouteEntry validator — AC #32 ───────────────────────────────


def test_pipeline_route_entry_predicate_only_is_valid():
    PipelineRouteEntry(predicate="always", pipeline_path=Path("presets/x.yaml"))


def test_pipeline_route_entry_default_only_is_valid():
    PipelineRouteEntry(default=True, pipeline_path=Path("presets/x.yaml"))


def test_pipeline_route_entry_rejects_both_predicate_and_default():
    with pytest.raises(ValidationError, match="exactly one of predicate or default"):
        PipelineRouteEntry(
            predicate="always", default=True, pipeline_path=Path("presets/x.yaml"),
        )


def test_pipeline_route_entry_rejects_neither_predicate_nor_default():
    with pytest.raises(ValidationError, match="exactly one of predicate or default"):
        PipelineRouteEntry(pipeline_path=Path("presets/x.yaml"))


# ── Shipped preset resource sanity ──────────────────────────────────────


def test_preset_chunk_fts_loadable():
    chunk_yaml = importlib.resources.files("pydocs_mcp.presets").joinpath("chunk_fts.yaml")
    assert chunk_yaml.is_file()


def test_preset_member_like_loadable():
    member_yaml = importlib.resources.files("pydocs_mcp.presets").joinpath("member_like.yaml")
    assert member_yaml.is_file()


def test_preset_default_config_loadable():
    default_yaml = importlib.resources.files("pydocs_mcp.presets").joinpath("default_config.yaml")
    assert default_yaml.is_file()


# ── pipeline_path allowlist ─────────────────────────────────────────────


def test_pipeline_path_rejects_absolute_outside_allowed_roots(tmp_path):
    """An absolute path that escapes the allowlist (e.g. ``/etc/shadow``)
    must raise ValueError before any file read happens."""
    outside = tmp_path / "evil.yaml"
    outside.write_text("name: evil\nstages: []\n")
    with pytest.raises(ValueError, match="pipeline_path must be inside"):
        # No user-config path → only presets/ is allowed.
        _resolve_pipeline_path(outside, user_config_path=None)


def test_pipeline_path_rejects_symlink_traversal(tmp_path):
    """A symlink inside the shipped presets/ dir that points outside the
    allowlist must be rejected after resolve() follows it."""
    # We simulate the attack by creating a user-config dir, a symlink inside
    # it pointing outside the allowlist, and supplying the user_config_path
    # so the user-config dir is part of allowed_roots.
    user_cfg_dir = tmp_path / "cfg"
    user_cfg_dir.mkdir()
    user_cfg_file = user_cfg_dir / "pydocs-mcp.yaml"
    user_cfg_file.write_text("log_level: info\n")

    outside_target = tmp_path / "outside.yaml"
    outside_target.write_text("name: bad\n")

    link = user_cfg_dir / "bad.yaml"
    os.symlink(outside_target, link)

    with pytest.raises(ValueError, match="pipeline_path must be inside"):
        _resolve_pipeline_path(Path("bad.yaml"), user_config_path=user_cfg_file)


def test_pipeline_path_accepts_relative_inside_user_config(tmp_path):
    """A relative path alongside the user config resolves successfully."""
    user_cfg_dir = tmp_path / "cfg"
    user_cfg_dir.mkdir()
    user_cfg_file = user_cfg_dir / "pydocs-mcp.yaml"
    user_cfg_file.write_text("log_level: info\n")
    sibling = user_cfg_dir / "my_pipeline.yaml"
    sibling.write_text("name: custom\nstages: []\n")
    resolved = _resolve_pipeline_path(Path("my_pipeline.yaml"), user_config_path=user_cfg_file)
    assert resolved == sibling.resolve()


def test_pipeline_path_accepts_shipped_presets_relative(tmp_path):
    """Back-compat: the bundled presets stay reachable via ``presets/foo.yaml``."""
    # chunk_fts.yaml is shipped inside pydocs_mcp/presets/
    resolved = _resolve_pipeline_path(Path("presets/chunk_fts.yaml"), user_config_path=None)
    assert resolved.name == "chunk_fts.yaml"
