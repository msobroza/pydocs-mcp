"""Tests for AppConfig YAML layering + PipelineRouteEntry validator (spec §5.9)."""
from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
from unittest.mock import patch

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
    shipped ``defaults/default_config.yaml`` baseline layer (spec §5.9, AC #14)."""
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
    PipelineRouteEntry(predicate="always", pipeline_path=Path("pipelines/x.yaml"))


def test_pipeline_route_entry_default_only_is_valid():
    PipelineRouteEntry(default=True, pipeline_path=Path("pipelines/x.yaml"))


def test_pipeline_route_entry_rejects_both_predicate_and_default():
    with pytest.raises(ValidationError, match="exactly one of predicate or default"):
        PipelineRouteEntry(
            predicate="always", default=True, pipeline_path=Path("pipelines/x.yaml"),
        )


def test_pipeline_route_entry_rejects_neither_predicate_nor_default():
    with pytest.raises(ValidationError, match="exactly one of predicate or default"):
        PipelineRouteEntry(pipeline_path=Path("pipelines/x.yaml"))


# ── Shipped preset resource sanity ──────────────────────────────────────


def test_pipeline_chunk_search_loadable():
    chunk_yaml = importlib.resources.files("pydocs_mcp.pipelines").joinpath("chunk_search.yaml")
    assert chunk_yaml.is_file()


def test_pipeline_member_search_loadable():
    member_yaml = importlib.resources.files("pydocs_mcp.pipelines").joinpath("member_search.yaml")
    assert member_yaml.is_file()


def test_default_config_loadable():
    default_yaml = importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")
    assert default_yaml.is_file()


# ── pipeline_path allowlist ─────────────────────────────────────────────


def test_pipeline_path_rejects_absolute_outside_allowed_roots(tmp_path):
    """An absolute path that escapes the allowlist (e.g. ``/etc/shadow``)
    must raise ValueError before any file read happens."""
    outside = tmp_path / "evil.yaml"
    outside.write_text("name: evil\nstages: []\n")
    with pytest.raises(ValueError, match="pipeline_path must be inside"):
        # No user-config path → only pipelines/ is allowed.
        _resolve_pipeline_path(outside, user_config_path=None)


def test_pipeline_path_rejects_symlink_traversal(tmp_path):
    """A symlink inside the shipped pipelines/ dir that points outside the
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


def test_pipeline_path_accepts_shipped_pipelines_relative(tmp_path):
    """The bundled pipelines stay reachable via ``pipelines/foo.yaml``."""
    # chunk_search.yaml is shipped inside pydocs_mcp/pipelines/
    resolved = _resolve_pipeline_path(Path("pipelines/chunk_search.yaml"), user_config_path=None)
    assert resolved.name == "chunk_search.yaml"


def test_pipeline_path_user_local_pipelines_overrides_shipped(tmp_path):
    """Search-path semantics: when the user has a local ``./pipelines/foo.yaml``
    next to their config, it overrides the shipped one with the same name."""
    user_cfg_dir = tmp_path / "cfg"
    user_cfg_dir.mkdir()
    user_cfg_file = user_cfg_dir / "pydocs-mcp.yaml"
    user_cfg_file.write_text("log_level: info\n")
    local_pipelines = user_cfg_dir / "pipelines"
    local_pipelines.mkdir()
    local_override = local_pipelines / "chunk_search.yaml"
    local_override.write_text("name: custom\nstages: []\n")
    resolved = _resolve_pipeline_path(
        Path("pipelines/chunk_search.yaml"), user_config_path=user_cfg_file,
    )
    assert resolved == local_override.resolve()


def test_pipeline_path_falls_back_to_shipped_when_user_local_missing(tmp_path):
    """If the user has a pydocs-mcp.yaml but no local ``./pipelines/`` dir,
    ``pipelines/foo.yaml`` still resolves to the shipped bundle (no regression)."""
    user_cfg_dir = tmp_path / "cfg"
    user_cfg_dir.mkdir()
    user_cfg_file = user_cfg_dir / "pydocs-mcp.yaml"
    user_cfg_file.write_text("log_level: info\n")
    # No local pipelines/ subdir — falls through to shipped
    resolved = _resolve_pipeline_path(
        Path("pipelines/chunk_search.yaml"), user_config_path=user_cfg_file,
    )
    assert resolved.name == "chunk_search.yaml"
    assert "pydocs_mcp/pipelines" in str(resolved)


def test_pipeline_path_legacy_presets_prefix_raises_migration_error(tmp_path):
    """A legacy ``presets/chunk_fts.yaml`` path (pre-rename) raises a clear
    ValueError pointing at the new convention, not a confusing FileNotFoundError."""
    with pytest.raises(ValueError, match="presets/.*renamed to 'pipelines/'"):
        _resolve_pipeline_path(Path("presets/chunk_fts.yaml"), user_config_path=None)


# ── Sub-PR #5 — ExtractionConfig slotting (spec §11) ────────────────


def test_appconfig_includes_extraction_defaults():
    """``AppConfig.load()`` surfaces the shipped ``extraction:`` block —
    every sub-section populated with its Pydantic-default values."""
    from pydocs_mcp.extraction.config import ExtractionConfig

    config = AppConfig.load()
    assert isinstance(config.extraction, ExtractionConfig)
    # Shipped YAML drives these, not the Pydantic defaults — the two
    # should agree, but we assert on the YAML values to catch drift
    # between code and YAML.
    # F11: chunker selection is decorator-driven (chunker_registry); the
    # legacy ``by_extension`` dict was dead config and got removed. The
    # shipped YAML must NOT declare it (Pydantic extra='forbid' would
    # reject the merged config).
    assert not hasattr(config.extraction.chunking, "by_extension")
    assert config.extraction.chunking.markdown.max_heading_level == 3
    assert config.extraction.chunking.notebook.include_outputs is False
    assert config.extraction.discovery.project.include_extensions == [
        ".py", ".md", ".ipynb",
    ]
    assert config.extraction.discovery.project.max_file_size_bytes == 500_000
    assert config.extraction.discovery.dependency.max_file_size_bytes == 500_000
    assert config.extraction.members.inspect_depth == 1
    assert config.extraction.members.members_per_module_cap == 120
    assert config.extraction.ingestion.pipeline_path is None


def test_appconfig_extraction_yaml_round_trips(tmp_path):
    """User YAML overrides partial extraction settings; unmentioned keys
    keep their shipped defaults — proves ``extraction:`` participates in
    the usual YAML-overlay semantics."""
    user_file = tmp_path / "pydocs-mcp.yaml"
    user_file.write_text(
        "extraction:\n"
        "  chunking:\n"
        "    markdown:\n"
        "      max_heading_level: 6\n"
        "  members:\n"
        "    inspect_depth: 3\n"
    )
    config = AppConfig.load(explicit_path=user_file)
    # Overridden:
    assert config.extraction.chunking.markdown.max_heading_level == 6
    assert config.extraction.members.inspect_depth == 3
    # Untouched — still at shipped defaults.
    assert config.extraction.chunking.markdown.min_heading_level == 1
    assert config.extraction.discovery.project.include_extensions == [
        ".py", ".md", ".ipynb",
    ]
    assert config.extraction.members.members_per_module_cap == 120


# ── Ingestion pipeline-hash caching (I14) ───────────────────────────────


def test_compute_ingestion_pipeline_hash_cached(tmp_path: Path) -> None:
    """The ingestion YAML must be read at most once per AppConfig instance.

    Without caching, each ``compute_ingestion_pipeline_hash()`` /
    ``ingestion_pipeline_hash`` access re-opens the YAML file and re-hashes
    its bytes — wasted work, since the file path + content are fixed for the
    life of the AppConfig. Spy on ``Path.read_bytes`` over 3 accesses and
    assert at most one call (after potential warm-up reads from
    ``AppConfig.load`` settling).
    """
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("name: test\nstages: []\n")
    cfg = AppConfig.load()
    cfg.extraction.ingestion.pipeline_path = yaml_path

    # Wrap read_bytes so we count calls against ``yaml_path`` specifically —
    # AppConfig.load() may have opened other files (shipped YAMLs) that we
    # don't want to count here.
    real_read_bytes = Path.read_bytes
    yaml_reads = 0

    def counting_read_bytes(self: Path) -> bytes:
        nonlocal yaml_reads
        if self.resolve() == yaml_path.resolve():
            yaml_reads += 1
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", counting_read_bytes):
        h1 = cfg.ingestion_pipeline_hash
        h2 = cfg.ingestion_pipeline_hash
        h3 = cfg.ingestion_pipeline_hash

    # cached_property — file is read once across 3 accesses
    assert yaml_reads <= 1, (
        f"expected ingestion YAML to be read at most once, got {yaml_reads} reads"
    )
    assert h1 == h2 == h3
    # Method form must still exist and produce the same value (backward compat).
    assert cfg.compute_ingestion_pipeline_hash() == h1
