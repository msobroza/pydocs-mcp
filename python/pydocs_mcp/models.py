"""Canonical domain models for pydocs-mcp.

This module is the single source of truth for the domain vocabulary — see
docs/superpowers/specs/2026-04-19-sub-pr-1-naming-and-models-design.md §5.

All dataclasses are frozen + slotted. All enums subclass enum.StrEnum so values
round-trip through SQLite TEXT columns and JSON without glue code.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar

from pydantic import ConfigDict, field_validator, model_validator
from pydantic.dataclasses import dataclass as pyd_dataclass


class ChunkOrigin(StrEnum):
    PROJECT_MODULE_DOC       = "project_module_doc"
    PROJECT_CODE_SECTION     = "project_code_section"
    DEPENDENCY_CODE_SECTION  = "dependency_code_section"
    DEPENDENCY_DOC_FILE      = "dependency_doc_file"
    DEPENDENCY_README        = "dependency_readme"
    DEPENDENCY_MODULE_DOC    = "dependency_module_doc"
    COMPOSITE_OUTPUT         = "composite_output"


class MemberKind(StrEnum):
    FUNCTION = "function"
    CLASS    = "class"
    METHOD   = "method"


class PackageOrigin(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"


class SearchScope(StrEnum):
    PROJECT_ONLY      = "project_only"
    DEPENDENCIES_ONLY = "dependencies_only"
    ALL               = "all"


class MetadataFilterFormat(StrEnum):
    MULTIFIELD    = "multifield"
    FILTER_TREE   = "filter_tree"
    CHROMADB      = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT        = "qdrant"


class ChunkFilterField(StrEnum):
    """Canonical metadata keys for Chunk queries (keys in the `metadata` mapping,
    not dataclass fields). Used by MCP handlers to build pre_filter dicts."""
    PACKAGE = "package"
    TITLE   = "title"
    ORIGIN  = "origin"
    MODULE  = "module"
    SCOPE   = "scope"


class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    annotation: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class Package:
    kind: ClassVar[str] = "package"
    name: str
    version: str
    summary: str
    homepage: str
    dependencies: tuple[str, ...]
    content_hash: str
    origin: PackageOrigin


@dataclass(frozen=True, slots=True)
class Chunk:
    """Unit of retrieval. `text` is the primary payload; everything else
    (package, title, origin, module) lives in metadata keyed by
    ChunkFilterField.*.value. Composite chunks (formatter output) set
    metadata['origin'] == ChunkOrigin.COMPOSITE_OUTPUT.value.

    Retrieval-time fields (relevance, retriever_name) are None until a
    retriever populates them."""
    kind: ClassVar[str] = "chunk"
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ModuleMember:
    """A named Python API member (function, class, method). Fully generic —
    all structural fields (name, module, package, kind, signature, docstring,
    return_annotation, parameters) live in metadata. The Rust parser produces
    a typed ParsedMember (see _fallback.py / src/lib.rs); the indexer
    converts into this form at the boundary."""
    kind: ClassVar[str] = "module_member"
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ChunkList:
    kind: ClassVar[str] = "chunk_list"
    items: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class ModuleMemberList:
    kind: ClassVar[str] = "module_member_list"
    items: tuple[ModuleMember, ...]


PipelineResultItem = ChunkList | ModuleMemberList


@pyd_dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class SearchQuery:
    """Pydantic dataclass with construction-time validation.

    `pre_filter` and `post_filter` are native mappings in the format
    named by `pre_filter_format` / `post_filter_format`. Syntax is
    validated at construction time against
    `pydocs_mcp.storage.filters.format_registry` (spec §5.5, AC #12).
    The filter-registry import is deferred inside the validator body so
    that `storage.filters` — which does not import from `models.py` —
    can keep importing safely.
    """
    terms: str
    max_results: int = 8
    pre_filter: Mapping[str, Any] | None = None
    post_filter: Mapping[str, Any] | None = None
    pre_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD
    post_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD

    @field_validator("terms")
    @classmethod
    def _terms_non_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("terms must be non-empty")
        return stripped

    @field_validator("max_results")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_results must be positive")
        return v

    @model_validator(mode="after")
    def _validate_filter_syntax(self) -> "SearchQuery":
        # Lazy import to avoid any risk of a circular import at module load:
        # storage.filters does not import from models, but keeping the import
        # inside the validator body is the cleanest way to keep the direction
        # of dependency one-way (models ← storage.filters).
        from pydocs_mcp.storage.filters import format_registry

        for raw_filter, fmt in (
            (self.pre_filter, self.pre_filter_format),
            (self.post_filter, self.post_filter_format),
        ):
            if raw_filter is not None:
                format_registry[fmt].validate(raw_filter)
        return self


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Pipeline-runner output: the typed result plus its originating query and
    the measured duration. Used as the return type of use-case services."""
    result: PipelineResultItem
    query: SearchQuery
    duration_ms: float = 0.0
