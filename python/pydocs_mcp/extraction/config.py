"""ExtractionConfig Pydantic models + hardcoded ``_EXCLUDED_DIRS`` (spec §11.1).

Chunker selection is decorator-driven (see
:data:`~pydocs_mcp.extraction.serialization.chunker_registry`); there is
no YAML knob to override the per-extension chunker. The earlier
``ChunkingConfig.by_extension`` field was never read by ChunkingStage
and got dropped (/ultrareview F11).


Policy (decision #6b): the **extension allowlist** is narrowable via YAML
(``include_extensions``); the **directory blocklist** is
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
:class:`~pydocs_mcp.extraction.protocols.Chunker` AND amending
this allowlist — can't be done via YAML alone."""


_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".eggs",
        "egg-info",
        "node_modules",
        "build",
        "dist",
        "target",
        "htmlcov",
        ".coverage",
        ".cache",
        "site-packages",
    }
)
"""Directory names excluded from file discovery — HARDCODED by design (spec
decision #6b). NOT exposed as a YAML field; trying to set
``extraction.discovery.project.exclude_dirs: [...]`` hits Pydantic
``extra="forbid"`` at load time (spec AC #6b)."""


def path_under_excluded(
    filepath: str,
    excluded: frozenset[str] = _EXCLUDED_DIRS,
) -> bool:
    """True iff any path component of ``filepath`` is in ``excluded``.

    Canonical helper used by every file-discovery path:
    - :func:`pydocs_mcp.extraction.strategies.discovery._shared._in_excluded_dir`
      wraps it for the dependency-file walk.
    - :class:`pydocs_mcp.extraction.strategies.members.AstMemberExtractor`
      uses it to post-filter ``walk_py_files`` output against the
      canonical policy.

    Path splitting normalises backslashes to forward slashes BEFORE
    splitting on ``/``, so Rust output (always forward slashes regardless
    of platform) and Python output (platform-native separators) both
    flow through the same component-membership check. Cheap — frozenset
    lookup is O(1) per component.
    """
    parts = filepath.replace("\\", "/").split("/")
    return any(part in excluded for part in parts)


class MarkdownConfig(BaseModel):
    """Heading-depth bounds for :class:`HeadingMarkdownChunker`."""

    model_config = ConfigDict(extra="forbid")

    min_heading_level: int = 1
    max_heading_level: int = 3


class NotebookConfig(BaseModel):
    """Options for :class:`NotebookChunker`."""

    model_config = ConfigDict(extra="forbid")

    include_outputs: bool = False


class ChunkingConfig(BaseModel):
    """Per-chunker tunables (markdown heading bounds, notebook outputs).

    Chunker selection lives in the
    :data:`~pydocs_mcp.extraction.serialization.chunker_registry` —
    chunkers decorate themselves with ``@_register_chunker(ext)`` and
    :class:`~pydocs_mcp.extraction.pipeline.stages.ChunkingStage` looks
    up ``chunker_registry[ext]`` per file. There is intentionally NO
    YAML-level chunker override: a previous ``by_extension`` field was
    declared but never read by ChunkingStage (/ultrareview F11) — it
    was dead config that misled readers into thinking the dispatch
    table was data-driven. Adding a new extension requires registering
    a chunker via the decorator, not editing YAML.
    """

    model_config = ConfigDict(extra="forbid")

    markdown: MarkdownConfig = Field(default_factory=MarkdownConfig)
    notebook: NotebookConfig = Field(default_factory=NotebookConfig)


class DiscoveryScopeConfig(BaseModel):
    """Per-context discovery scope — project vs dependency.

    NOTE: there is deliberately NO ``exclude_dirs`` field — the blocklist
    lives in :data:`_EXCLUDED_DIRS` (see policy note at module docstring).
    Users can narrow ``include_extensions`` but cannot widen the dir
    blocklist; ``extra="forbid"`` catches stray keys at load time.
    """

    model_config = ConfigDict(extra="forbid")

    include_extensions: list[str] = Field(default_factory=lambda: [".py", ".md", ".ipynb"])
    # 1MB, not 500KB: a real 561KB module (mlc_llm dispatch table) was
    # silently skipped under the old cap, imposing an unwinnable recall
    # ceiling on every retrieval method (PAGEINDEX_DIVS.md F3).
    max_file_size_bytes: int = 1_000_000

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
    """Tunables for :class:`InspectMemberExtractor` / :class:`AstMemberExtractor`.

    Integer fields guard against silent zero-out: ``ge=1`` rejects
    ``inspect_depth: 0`` (no submodule traversal — would index nothing
    deeper than the root) and ``members_per_module_cap: 0`` (cap fires
    on iter 0 → zero symbols collected per module).

    Per-field truncation limits (``signature_max_chars`` /
    ``docstring_max_chars``) are peers of ``members_per_module_cap`` in
    bounding row size — without YAML access they were dead constants.
    Defaults match the pre-YAML constants in
    :mod:`pydocs_mcp.extraction.strategies._dep_helpers`. Validation
    runs at YAML load time so a fat-fingered config fails loud.
    """

    model_config = ConfigDict(extra="forbid")

    inspect_depth: int = Field(default=1, ge=1)
    members_per_module_cap: int = Field(default=120, ge=1)
    signature_max_chars: int = Field(default=200, ge=1)
    docstring_max_chars: int = Field(default=1024, ge=1)


class IngestionConfig(BaseModel):
    """Ingestion-pipeline YAML override.

    Default ``None`` → the shipped ``pipelines/ingestion.yaml``.
    User override resolves via the sub-PR #2 path allowlist (AC #33)
    — candidates must live inside the shipped pipelines directory or the
    directory holding the user's config file; symlinks resolve before the
    check.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_path: Path | None = None


class ExtractionConfig(BaseModel):
    """Root extraction config — slots into :class:`AppConfig` via
    ``extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)``.
    """

    model_config = ConfigDict(extra="forbid")

    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    members: MembersConfig = Field(default_factory=MembersConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
