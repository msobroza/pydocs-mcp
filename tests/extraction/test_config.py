"""Tests for ExtractionConfig + _EXCLUDED_DIRS policy (spec §11.1, AC #5/#6/#6b).

Invariants:
- All defaults load without a YAML file.
- Extension allowlist is narrowable (``[".py"]`` works).
- Extension allowlist cannot be widened (``[".rst"]`` raises).
- ``_EXCLUDED_DIRS`` is a module-level ``frozenset`` (non-overridable at
  runtime).
- :class:`DiscoveryScopeConfig` does NOT expose an ``exclude_dirs`` field
  (spec AC #6b).
- All models use ``extra="forbid"`` — stray keys raise.
- ``by_extension`` validator catches unsupported extensions.
- ``ExtractionConfig`` round-trips via ``model_dump`` + re-construction.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.extraction.config import (
    ALLOWED_EXTENSIONS,
    ChunkingConfig,
    DiscoveryConfig,
    DiscoveryScopeConfig,
    ExtractionConfig,
    IngestionConfig,
    MarkdownConfig,
    MembersConfig,
    NotebookConfig,
    _EXCLUDED_DIRS,
)


def test_extraction_config_defaults_load():
    """Bare :class:`ExtractionConfig` builds with shipped defaults."""
    cfg = ExtractionConfig()
    assert cfg.chunking.by_extension == {
        ".py": "ast_python",
        ".md": "heading_markdown",
        ".ipynb": "notebook",
    }
    assert cfg.chunking.markdown.min_heading_level == 1
    assert cfg.chunking.markdown.max_heading_level == 3
    assert cfg.chunking.notebook.include_outputs is False
    assert cfg.discovery.project.include_extensions == [".py", ".md", ".ipynb"]
    assert cfg.discovery.project.max_file_size_bytes == 500_000
    assert cfg.discovery.dependency.include_extensions == [".py", ".md", ".ipynb"]
    assert cfg.members.inspect_depth == 1
    assert cfg.members.members_per_module_cap == 120
    assert cfg.ingestion.pipeline_path is None


def test_allowed_extensions_is_frozenset():
    """``ALLOWED_EXTENSIONS`` must be a frozenset — so nobody's test can
    mutate it and silently widen the allowlist for other tests."""
    assert isinstance(ALLOWED_EXTENSIONS, frozenset)
    assert ALLOWED_EXTENSIONS == frozenset({".py", ".md", ".ipynb"})


def test_excluded_dirs_is_module_level_frozenset():
    """Spec AC #6b: ``_EXCLUDED_DIRS`` is a frozenset at module scope;
    users cannot override it at runtime (also not via YAML — see
    ``test_discovery_scope_config_forbids_exclude_dirs``)."""
    assert isinstance(_EXCLUDED_DIRS, frozenset)
    # Common noisy / secret-bearing directories.
    for d in (".git", ".venv", "site-packages", "node_modules", "__pycache__"):
        assert d in _EXCLUDED_DIRS, (
            f"{d!r} must be blocklisted (security / index-bloat invariant)"
        )


def test_discovery_scope_config_forbids_exclude_dirs():
    """Spec AC #6b guardrail: ``DiscoveryScopeConfig.model_fields`` must NOT
    contain ``exclude_dirs``. Attempting to set it via YAML / init hits
    Pydantic ``extra="forbid"`` and raises :class:`ValidationError`."""
    assert "exclude_dirs" not in DiscoveryScopeConfig.model_fields, (
        "exclude_dirs must not be a declared field — blocklist is hardcoded"
    )
    with pytest.raises(ValidationError, match="exclude_dirs"):
        DiscoveryScopeConfig(exclude_dirs=["my_secret_dir"])


def test_include_extensions_narrow_ok():
    """Narrowing the extension allowlist is legal — e.g. a .py-only project."""
    cfg = DiscoveryScopeConfig(include_extensions=[".py"])
    assert cfg.include_extensions == [".py"]


def test_include_extensions_widen_rejected():
    """Widening the extension allowlist is rejected — the chunker registry
    can only dispatch on ``ALLOWED_EXTENSIONS``."""
    with pytest.raises(ValidationError, match="unsupported extensions"):
        DiscoveryScopeConfig(include_extensions=[".py", ".rst"])


def test_by_extension_widen_rejected():
    """``ChunkingConfig.by_extension`` shares the same allowlist guard —
    spec AC #6."""
    with pytest.raises(ValidationError, match="unsupported extensions"):
        ChunkingConfig(by_extension={".yaml": "my_yaml"})


def test_extraction_config_forbids_unknown_top_level_key():
    """``extra="forbid"`` at the root model — stops typos like
    ``extracton:`` (missing i) from being silently ignored."""
    with pytest.raises(ValidationError):
        ExtractionConfig(bogus_field=True)


def test_every_model_forbids_extras():
    """Every nested model uses ``extra="forbid"`` — fail-fast on YAML typos."""
    for model in (
        MarkdownConfig, NotebookConfig, ChunkingConfig, DiscoveryScopeConfig,
        DiscoveryConfig, MembersConfig, IngestionConfig, ExtractionConfig,
    ):
        with pytest.raises(ValidationError):
            model(bogus=1)  # type: ignore[call-arg]


def test_model_dump_round_trips():
    """``ExtractionConfig`` re-loads from its own ``model_dump()`` — proves
    there's no unroundtrippable transformation hiding in a default_factory."""
    original = ExtractionConfig()
    dumped = original.model_dump()
    rebuilt = ExtractionConfig(**dumped)
    assert rebuilt == original


def test_markdown_heading_levels_tunable():
    """Markdown tunables can be narrowed — user YAML override happy path."""
    cfg = MarkdownConfig(min_heading_level=2, max_heading_level=4)
    assert cfg.min_heading_level == 2
    assert cfg.max_heading_level == 4


def test_ingestion_pipeline_path_optional():
    """``IngestionConfig.pipeline_path`` is optional — ``None`` means
    use the shipped preset. User YAML can override."""
    cfg = IngestionConfig()
    assert cfg.pipeline_path is None
    override = IngestionConfig(pipeline_path="./my_ingestion.yaml")
    assert override.pipeline_path.name == "my_ingestion.yaml"
