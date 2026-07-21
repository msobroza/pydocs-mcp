"""Tests for ExtractionConfig + the directory-exclusion floor (spec §11.1).

Invariants:
- All defaults load without a YAML file.
- Extension allowlist is narrowable (``[".py"]`` works).
- Extension allowlist enforces the ADR 0021 T1 ceiling: text/config + code
  extensions are accepted; binary/asset junk (``[".png"]``) still raises.
- ``_EXCLUDED_DIRS`` is a module-level ``frozenset`` — the hardcoded,
  non-removable FLOOR (decision #6b as amended by the 2026-07-13
  exclude-dirs design: user ``exclude_dirs`` entries are additive-only).
- :class:`DiscoveryScopeConfig` declares ``exclude_dirs`` (AC-14) and
  validates entries via the shared ``split_exclude_entries`` (AC-15).
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

# ADR 0021 T1: the widened DEFAULT include_extensions = existing + text/config.
# Code extensions (.js .ts .tsx .c .h .rs) are in ALLOWED_EXTENSIONS but NOT
# the default — they are ceiling-only opt-in.
_EXPECTED_DEFAULT_EXTENSIONS = [
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
]


def test_extraction_config_defaults_load():
    """Bare :class:`ExtractionConfig` builds with shipped defaults."""
    cfg = ExtractionConfig()
    # F11: by_extension was dead config (ChunkingStage dispatches via
    # chunker_registry decorator, never read the YAML field). The
    # field is gone; chunker selection is decorator-driven only.
    assert not hasattr(cfg.chunking, "by_extension")
    assert cfg.chunking.markdown.min_heading_level == 1
    assert cfg.chunking.markdown.max_heading_level == 3
    assert cfg.chunking.notebook.include_outputs is False
    # ADR 0021 T1: default widened to existing + text/config.
    assert cfg.discovery.project.include_extensions == _EXPECTED_DEFAULT_EXTENSIONS
    # 1MB: a 561KB real-world module was silently skipped under 500KB and
    # capped retrieval recall for every method (PAGEINDEX_DIVS.md F3).
    assert cfg.discovery.project.max_file_size_bytes == 1_000_000
    assert cfg.discovery.dependency.include_extensions == _EXPECTED_DEFAULT_EXTENSIONS
    assert cfg.members.inspect_depth == 1
    assert cfg.members.members_per_module_cap == 120
    assert cfg.ingestion.pipeline_path is None


def test_allowed_extensions_is_frozenset():
    """``ALLOWED_EXTENSIONS`` must be a frozenset — so nobody's test can
    mutate it and silently widen the allowlist for other tests."""
    assert isinstance(ALLOWED_EXTENSIONS, frozenset)
    # ADR 0021 T1: the ceiling is the full census-scoped set — existing +
    # text/config (also the default) + code (ceiling-only opt-in).
    assert (
        frozenset(
            {
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
                ".js",
                ".ts",
                ".tsx",
                ".c",
                ".h",
                ".rs",
            }
        )
        == ALLOWED_EXTENSIONS
    )


def test_excluded_dirs_is_module_level_frozenset():
    """``_EXCLUDED_DIRS`` is a frozenset at module scope — the FLOOR of
    decision #6b as amended: user surfaces can only ADD exclusions on top
    (see ``test_discovery_scope_config_declares_exclude_dirs``); nothing
    can remove an entry from it at runtime or via YAML."""
    assert isinstance(_EXCLUDED_DIRS, frozenset)
    # Common noisy / secret-bearing directories + ADR 0021 vendored
    # second-language trees (extern/third_party = C/Rust, node_modules/
    # .yarn/bower_components = JS) — census: read-side noise, zero value.
    for d in (
        ".git",
        ".venv",
        "site-packages",
        "node_modules",
        "__pycache__",
        ".yarn",
        "bower_components",
        "extern",
        "third_party",
    ):
        assert d in _EXCLUDED_DIRS, f"{d!r} must be blocklisted (security / index-bloat invariant)"


def test_discovery_scope_config_declares_exclude_dirs():
    """AC-14, inverting the old #6b rejection test: ``exclude_dirs`` IS a
    declared field (decision D1 — the FLOOR stays hardcoded in
    ``_EXCLUDED_DIRS``; user entries are additive-only), defaulting to []."""
    assert "exclude_dirs" in DiscoveryScopeConfig.model_fields
    assert DiscoveryScopeConfig().exclude_dirs == []
    cfg = DiscoveryScopeConfig(exclude_dirs=["fixtures", "docs/generated"])
    assert cfg.exclude_dirs == ["fixtures", "docs/generated"]


def test_exclude_dirs_loads_through_app_config(tmp_path):
    """AC-14 end of the wire: a YAML overlay setting
    ``extraction.discovery.project.exclude_dirs`` loads through
    ``AppConfig.load`` — no more ``extra="forbid"`` rejection for this key."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["fixtures"]\n'
    )
    config = AppConfig.load(explicit_path=overlay)
    assert config.extraction.discovery.project.exclude_dirs == ["fixtures"]
    assert config.extraction.discovery.dependency.exclude_dirs == []


@pytest.mark.parametrize("bad_entry", ["/tmp/abs", "a/../b", ""])
def test_exclude_dirs_invalid_entry_rejected_at_load(tmp_path, bad_entry):
    """AC-15: escaping / empty entries fail at ``AppConfig.load`` with a
    ``ValidationError`` naming the field — the shared
    ``split_exclude_entries`` rules (D5), surfaced through pydantic."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        f'extraction:\n  discovery:\n    project:\n      exclude_dirs: ["{bad_entry}"]\n'
    )
    with pytest.raises(ValidationError, match="exclude_dirs"):
        AppConfig.load(explicit_path=overlay)


def test_exclude_dirs_floor_duplicate_is_allowed():
    """Spec §8: listing a floor entry (".git") is a harmless no-op under
    union semantics — allowed, never an error."""
    cfg = DiscoveryScopeConfig(exclude_dirs=[".git"])
    assert cfg.exclude_dirs == [".git"]


def test_include_extensions_narrow_ok():
    """Narrowing the extension allowlist is legal — e.g. a .py-only project."""
    cfg = DiscoveryScopeConfig(include_extensions=[".py"])
    assert cfg.include_extensions == [".py"]


def test_include_extensions_accepts_widened_allowlist():
    """ADR 0021 T1: the ceiling now admits text/config + code extensions.
    ``.rst`` (once rejected) and ``.rs`` (ceiling-only opt-in) are both
    accepted — a YAML overlay can name any ALLOWED_EXTENSIONS member."""
    cfg = DiscoveryScopeConfig(include_extensions=[".py", ".rst", ".toml", ".rs"])
    assert cfg.include_extensions == [".py", ".rst", ".toml", ".rs"]


def test_include_extensions_widen_rejected():
    """Widening beyond ALLOWED_EXTENSIONS is still rejected — binary/asset
    extensions are never allowlisted, so junk like ``.png``/``.exe`` can
    never be widened in (ADR 0021 T1 keeps the allowlist enforcement)."""
    with pytest.raises(ValidationError, match="unsupported extensions"):
        DiscoveryScopeConfig(include_extensions=[".py", ".png", ".exe"])


def test_chunking_config_rejects_legacy_by_extension_key():
    """F11: the dead ``by_extension`` field was removed. A user YAML
    that still sets it must trip ``extra='forbid'`` so the typo /
    pre-fix config gets flagged rather than silently ignored."""
    with pytest.raises(ValidationError):
        ChunkingConfig(by_extension={".yaml": "my_yaml"})


def test_extraction_config_forbids_unknown_top_level_key():
    """``extra="forbid"`` at the root model — stops typos like
    ``extracton:`` (missing i) from being silently ignored."""
    with pytest.raises(ValidationError):
        ExtractionConfig(bogus_field=True)


def test_every_model_forbids_extras():
    """Every nested model uses ``extra="forbid"`` — fail-fast on YAML typos."""
    for model in (
        MarkdownConfig,
        NotebookConfig,
        ChunkingConfig,
        DiscoveryScopeConfig,
        DiscoveryConfig,
        MembersConfig,
        IngestionConfig,
        ExtractionConfig,
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


# -- A3: ge=1 validators on MembersConfig ---------------------------


def test_members_per_module_cap_zero_rejected():
    """A3: cap=0 fires on iter 0 and zeros the entire symbol index
    silently. Pydantic ge=1 must fail loud at YAML load time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MembersConfig(members_per_module_cap=0)


def test_members_per_module_cap_negative_rejected():
    """Same guard for negative values (Pydantic int cast accepts them
    silently otherwise)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MembersConfig(members_per_module_cap=-5)


def test_inspect_depth_zero_rejected():
    """A3: depth=0 means "no traversal" — index returns 0 symbols for
    every dep. Reject at load time."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MembersConfig(inspect_depth=0)


def test_members_config_defaults_still_valid():
    """Sanity: ge=1 mustn't reject the shipped defaults (1 + 120)."""
    cfg = MembersConfig()
    assert cfg.inspect_depth == 1
    assert cfg.members_per_module_cap == 120


def test_signature_and_docstring_max_chars_tunable():
    """M1: signature_max_chars and docstring_max_chars are YAML-tunable
    peers of members_per_module_cap. Pin the defaults + override path."""
    cfg = MembersConfig()
    assert cfg.signature_max_chars == 200
    assert cfg.docstring_max_chars == 1024
    override = MembersConfig(signature_max_chars=500, docstring_max_chars=4096)
    assert override.signature_max_chars == 500
    assert override.docstring_max_chars == 4096


def test_signature_max_chars_zero_rejected():
    """Same ge=1 floor as the cap — sig=0 truncates to a single ellipsis."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MembersConfig(signature_max_chars=0)
