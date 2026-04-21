"""ExtractionConfig Pydantic models + hardcoded ``_EXCLUDED_DIRS`` (spec §11.1).

Policy (decision #6b): the **extension allowlist** is narrowable via YAML
(``include_extensions`` / ``by_extension``); the **directory blocklist** is
HARDCODED in :data:`_EXCLUDED_DIRS` (not YAML-overridable) because
un-excluded ``.git`` / ``.venv`` / ``site-packages`` would leak secrets,
balloon the FTS index, and break inspect-mode imports. Users can narrow the
allowlist, never widen the blocklist. Pydantic ``extra="forbid"`` on
:class:`DiscoveryScopeConfig` surfaces any attempt to add an
``exclude_dirs`` field with a :class:`~pydantic.ValidationError` at startup
(spec AC #6b).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".py", ".md", ".ipynb"})
"""File extensions the extraction pipeline is built to handle. Narrowing is
allowed via YAML; adding a new extension requires registering a matching
:class:`~pydocs_mcp.extraction.protocols.Chunker` (Task 14+) AND amending
this allowlist — can't be done via YAML alone."""


_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs", "egg-info",
    "node_modules", "build", "dist", "target",
    "htmlcov", ".coverage", ".cache",
    "site-packages",
})
"""Directory names excluded from file discovery — HARDCODED by design (spec
decision #6b). NOT exposed as a YAML field; trying to set
``extraction.discovery.project.exclude_dirs: [...]`` hits Pydantic
``extra="forbid"`` at load time (spec AC #6b)."""


class MarkdownConfig(BaseModel):
    """Heading-depth bounds for :class:`HeadingMarkdownChunker` (Task 15)."""

    model_config = ConfigDict(extra="forbid")

    min_heading_level: int = 1
    max_heading_level: int = 3


class NotebookConfig(BaseModel):
    """Options for :class:`NotebookChunker` (Task 16)."""

    model_config = ConfigDict(extra="forbid")

    include_outputs: bool = False


class ChunkingConfig(BaseModel):
    """Per-extension chunker selection + chunker-specific tunables."""

    model_config = ConfigDict(extra="forbid")

    by_extension: dict[str, str] = Field(
        default_factory=lambda: {
            ".py": "ast_python",
            ".md": "heading_markdown",
            ".ipynb": "notebook",
        }
    )
    markdown: MarkdownConfig = Field(default_factory=MarkdownConfig)
    notebook: NotebookConfig = Field(default_factory=NotebookConfig)

    @field_validator("by_extension")
    @classmethod
    def _enforce_allowlist(cls, v: dict[str, str]) -> dict[str, str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(
                f"extraction.chunking.by_extension: unsupported extensions "
                f"{sorted(bad)}; must be subset of {sorted(ALLOWED_EXTENSIONS)}"
            )
        return v


class DiscoveryScopeConfig(BaseModel):
    """Per-context discovery scope — project vs dependency.

    NOTE: there is deliberately NO ``exclude_dirs`` field — the blocklist
    lives in :data:`_EXCLUDED_DIRS` (see policy note at module docstring).
    Users can narrow ``include_extensions`` but cannot widen the dir
    blocklist; ``extra="forbid"`` catches stray keys at load time.
    """

    model_config = ConfigDict(extra="forbid")

    include_extensions: list[str] = Field(
        default_factory=lambda: [".py", ".md", ".ipynb"]
    )
    max_file_size_bytes: int = 500_000

    @field_validator("include_extensions")
    @classmethod
    def _enforce_allowlist(cls, v: list[str]) -> list[str]:
        bad = set(v) - ALLOWED_EXTENSIONS
        if bad:
            raise ValueError(
                f"extraction.discovery.*.include_extensions: unsupported "
                f"extensions {sorted(bad)}; must be subset of "
                f"{sorted(ALLOWED_EXTENSIONS)}"
            )
        return v


class DiscoveryConfig(BaseModel):
    """Two scopes — project source vs installed dependency site-packages."""

    model_config = ConfigDict(extra="forbid")

    project: DiscoveryScopeConfig = Field(default_factory=DiscoveryScopeConfig)
    dependency: DiscoveryScopeConfig = Field(default_factory=DiscoveryScopeConfig)


class MembersConfig(BaseModel):
    """Tunables for :class:`InspectMemberExtractor` / :class:`AstMemberExtractor`."""

    model_config = ConfigDict(extra="forbid")

    inspect_depth: int = 1
    members_per_module_cap: int = 120


class IngestionConfig(BaseModel):
    """Ingestion-pipeline YAML override.

    Default ``None`` → the shipped ``presets/ingestion.yaml`` (Task 13).
    User override resolves via the sub-PR #2 path allowlist (AC #33)
    — candidates must live inside the shipped presets directory or the
    directory holding the user's config file; symlinks resolve before the
    check.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_path: Path | None = None


class ExtractionConfig(BaseModel):
    """Root extraction config — slots into :class:`AppConfig` via
    ``extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)``
    (Task 7).
    """

    model_config = ConfigDict(extra="forbid")

    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    members: MembersConfig = Field(default_factory=MembersConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
