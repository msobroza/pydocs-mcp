"""ExtractionConfig Pydantic models + hardcoded ``_EXCLUDED_DIRS`` (spec §11.1).

Chunker selection is decorator-driven (see
:data:`~pydocs_mcp.extraction.serialization.chunker_registry`); there is
no YAML knob to override the per-extension chunker. The earlier
``ChunkingConfig.by_extension`` field was never read by ChunkingStage
and got dropped (/ultrareview F11).


Policy (decision #6b, amended by the 2026-07-13 exclude-dirs design): the
**extension allowlist** is narrowable via YAML (``include_extensions``);
the **directory-exclusion FLOOR** is HARDCODED in :data:`_EXCLUDED_DIRS`
and non-removable — un-excluded ``.git`` / ``.venv`` / ``site-packages``
would leak secrets, balloon the FTS index, and break inspect-mode imports.
User exclusions are additive-only: ``exclude_dirs`` on
:class:`DiscoveryScopeConfig` (server YAML) and the indexed project's
``[tool.pydocs-mcp] exclude_dirs`` (see :mod:`pydocs_mcp.project_toml`)
union OVER the floor; no surface, and no syntax, can shrink it.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pydocs_mcp.project_toml import ProjectExcludeConfigError, split_exclude_entries

# ADR 0021 (multilanguage indexing, T1): the allowlist CEILING is the full
# census-scoped set — text/config AND code extensions. What is indexed by
# DEFAULT is per scope (ADR 0022, below): code extensions are default-ON for
# the project scope and opt-in for dependencies. The two per-scope
# ``_DEFAULT_*_INCLUDE_EXTENSIONS`` constants below are the single Python
# source for those defaults, wired by DiscoveryConfig's per-scope factories;
# YAML ``include_extensions`` still only narrows within this ceiling.
# Binary/asset extensions are never listed, so they can never be widened in.
_TEXT_CONFIG_EXTENSIONS: frozenset[str] = frozenset(
    {".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"}
)
_CODE_EXTENSIONS: frozenset[str] = frozenset({".js", ".ts", ".tsx", ".c", ".h", ".rs", ".java"})
ALLOWED_EXTENSIONS: frozenset[str] = (
    frozenset({".py", ".md", ".ipynb"}) | _TEXT_CONFIG_EXTENSIONS | _CODE_EXTENSIONS
)
"""File extensions the extraction pipeline is built to handle. Narrowing is
allowed via YAML; adding a new extension requires registering a matching
:class:`~pydocs_mcp.extraction.protocols.Chunker` AND amending
this allowlist — can't be done via YAML alone (ADR 0021 T1)."""

# ADR 0022 (multilang reference analyzers, spec D6): per-scope include
# defaults. The ADR 0021 census showed second-language code skews heavily
# vendored in DEPENDENCIES (e.g. 127 of matplotlib's 222 C/C++ files under
# extern/) while a user's own project code is exactly what they ask about —
# so code extensions are default-ON for project scope only. Single source of
# truth for both lists; defaults/default_config.yaml restates them (the
# sanctioned YAML duplication, which is also the partial-overlay backstop).
_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".ipynb",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".rst",
    ".txt",
    ".json",
)
_DEFAULT_PROJECT_INCLUDE_EXTENSIONS: tuple[str, ...] = (
    *_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS,
    ".js",
    ".ts",
    ".tsx",
    ".c",
    ".h",
    ".rs",
    ".java",
)


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
        # Vendored second-language source trees — ADR 0021 (multilanguage
        # indexing) grows the floor: the census found 127 of matplotlib's
        # 222 C/C++ files under extern/, i.e. read-side noise with zero
        # retrieval value. node_modules/.yarn/bower_components are the
        # JS-ecosystem vendored dirs; extern/third_party the C/Rust ones.
        "node_modules",
        ".yarn",
        "bower_components",
        "extern",
        "third_party",
        "build",
        "dist",
        "target",
        "htmlcov",
        ".coverage",
        ".cache",
        "site-packages",
        # eval gold-answer key — never index; data-leak guard for the
        # CrossCommitVuln QA dataset. The vendored gold answers
        # (cve/cwe/mechanism/files) must never enter any pydocs-mcp index or
        # they would leak needle answers into retrieval results. ADR 0021
        # keeps widening ALLOWED_EXTENSIONS, so the extension ceiling is not
        # a durable guarantee — this floor is (design §6.6).
        "crosscommitvuln",
    }
)
"""The hardcoded, non-removable directory-exclusion FLOOR (spec decision
#6b as amended): user surfaces — YAML ``exclude_dirs`` on
:class:`DiscoveryScopeConfig` and the project's ``[tool.pydocs-mcp]
exclude_dirs`` — can only ADD exclusions on top of this set."""


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


# ADR 0021 T2: TextSectionChunker tunables. Single source of truth for the
# defaults — the dataclass fields in ``chunkers/text_section.py`` import these
# constants so the Field default and the chunker default can never drift.
_DEFAULT_TEXT_WINDOW_LINES = 80
_DEFAULT_JSON_MAX_CHUNKS = 50


class TextSectionConfig(BaseModel):
    """Tunables for :class:`TextSectionChunker` (ADR 0021 T2).

    ``window_lines`` sizes the fixed-line fallback windows used for
    ``.rst``/``.txt`` files that carry no reStructuredText section titles.
    ``json_max_chunks`` caps how many top-level ``.json`` keys become section
    nodes — the fixture-flooding guard: a file exceeding the cap (or an
    unkeyed/minified blob) collapses to one truncated summary node instead.
    """

    model_config = ConfigDict(extra="forbid")

    window_lines: int = Field(default=_DEFAULT_TEXT_WINDOW_LINES, ge=1)
    json_max_chunks: int = Field(default=_DEFAULT_JSON_MAX_CHUNKS, ge=1)


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
    text_section: TextSectionConfig = Field(default_factory=TextSectionConfig)


class DiscoveryScopeConfig(BaseModel):
    """Per-context discovery scope — project vs dependency.

    ``exclude_dirs`` entries are ADDITIVE over the hardcoded
    :data:`_EXCLUDED_DIRS` floor (decision #6b as amended): bare names
    match any path component at any depth; entries containing ``/`` are
    walk-root-anchored subtree paths. No entry can shrink the floor.
    ``include_extensions`` remains narrow-only; ``extra="forbid"`` still
    catches genuinely unknown keys at load time.
    """

    model_config = ConfigDict(extra="forbid")

    # A BARE scope equals the dependency default; the effective per-scope
    # defaults live on DiscoveryConfig's factories below (spec D6). Kept as
    # a real default so directly-constructed scopes in tests stay valid.
    include_extensions: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS)
    )
    # 1MB, not 500KB: a real 561KB module (mlc_llm dispatch table) was
    # silently skipped under the old cap, imposing an unwinnable recall
    # ceiling on every retrieval method (PAGEINDEX_DIVS.md F3).
    max_file_size_bytes: int = 1_000_000
    exclude_dirs: list[str] = Field(default_factory=list)

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

    @field_validator("exclude_dirs")
    @classmethod
    def _validate_exclude_dirs(cls, v: list[str]) -> list[str]:
        # Delegate to the shared normalizer (design D5) so the TOML and
        # YAML surfaces can never drift; re-raise as ValueError so pydantic
        # wraps it into the usual startup ValidationError.
        try:
            split_exclude_entries(v)
        except ProjectExcludeConfigError as exc:
            raise ValueError(f"extraction.discovery.*.exclude_dirs: {exc}") from exc
        return v


class DiscoveryConfig(BaseModel):
    """Two scopes — project source vs installed dependency site-packages."""

    model_config = ConfigDict(extra="forbid")

    # Per-scope defaults (ADR 0022 / spec D6). NOTE: a default_factory fires
    # only when the whole scope key is absent from the input dict; a PARTIAL
    # overlay (e.g. only max_file_size_bytes) is backstopped by
    # defaults/default_config.yaml restating the lists through the AppConfig
    # layer merge — AC-29 pins that behavior.
    project: DiscoveryScopeConfig = Field(
        default_factory=lambda: DiscoveryScopeConfig(
            include_extensions=list(_DEFAULT_PROJECT_INCLUDE_EXTENSIONS)
        )
    )
    dependency: DiscoveryScopeConfig = Field(
        default_factory=lambda: DiscoveryScopeConfig(
            include_extensions=list(_DEFAULT_DEPENDENCY_INCLUDE_EXTENSIONS)
        )
    )


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
