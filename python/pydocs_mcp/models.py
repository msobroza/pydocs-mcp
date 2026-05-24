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
from typing import Any, ClassVar, Protocol, runtime_checkable

import numpy as np
from pydantic import ConfigDict, field_validator, model_validator
from pydantic.dataclasses import dataclass as pyd_dataclass

# ── Embedding types (spec §5.1) ──────────────────────────────────────────
# Aligned with FastEmbed (https://github.com/qdrant/fastembed):
#
#   Vector       = 1D np.ndarray, shape (dim,), dtype=float32.
#                  What TextEmbedding.embed() yields per document; what
#                  OpenAI returns; what TurboQuant IdMapIndex consumes.
#
#   MultiVector  = list[np.ndarray] — one 1D vector per token, ColBERT
#                  late-interaction shape. NOT persisted this PR (single-
#                  vector storage only); the type union accepts the shape
#                  so future late-interaction work doesn't break the
#                  Chunk model.
#
# SparseEmbedding (Protocol) — FastEmbed convention with .indices +
# .values numpy arrays. NOT in the Embedding union this PR; defined here
# so a future sparse-retrieval PR can extend Embedding without breaking
# changes.
Vector = np.ndarray
MultiVector = list[np.ndarray]
Embedding = Vector | MultiVector


@runtime_checkable
class SparseEmbedding(Protocol):
    """FastEmbed-compatible sparse embedding shape (forward-compat).

    Mirrors `fastembed.SparseEmbedding`'s public attributes (uint32
    indices + float32 values numpy arrays). Sparse retrieval is OUT OF
    SCOPE for this PR — this Protocol exists only so the typing layer
    is ready for it.
    """
    indices: np.ndarray   # uint32
    values:  np.ndarray   # float32


def is_multi_vector(emb: Embedding) -> bool:
    """True if `emb` is a multi-vector (list of 1D vectors, ColBERT-style).

    FastEmbed convention: single-vector embedders return `np.ndarray`;
    multi-vector embedders return `list[np.ndarray]`. The check is on
    the OUTER container type.
    """
    return isinstance(emb, list)


class ChunkOrigin(StrEnum):
    PROJECT_MODULE_DOC       = "project_module_doc"
    PROJECT_CODE_SECTION     = "project_code_section"
    DEPENDENCY_CODE_SECTION  = "dependency_code_section"
    DEPENDENCY_DOC_FILE      = "dependency_doc_file"
    DEPENDENCY_README        = "dependency_readme"
    DEPENDENCY_MODULE_DOC    = "dependency_module_doc"
    COMPOSITE_OUTPUT         = "composite_output"
    # sub-PR #5 §4.2: origins emitted by DocumentNode extraction strategies.
    # Tagged on Chunk.metadata["origin"] so retrievers / filters can route on
    # "where in the tree did this come from?". CODE_EXAMPLE nodes inherit the
    # origin of their parent (PYTHON_DEF or MARKDOWN_SECTION) — the NodeKind
    # carries the "is this a code example" distinction.
    PYTHON_DEF               = "python_def"
    MARKDOWN_SECTION         = "markdown_section"
    NOTEBOOK_MARKDOWN_CELL   = "notebook_markdown_cell"
    NOTEBOOK_CODE_CELL       = "notebook_code_cell"


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
    # sub-PR #5 §4.5: filter keys for tree-derived chunks — SOURCE_PATH
    # selects by the originating file (reference-graph lookups), CONTENT_HASH
    # deduplicates identical chunks across re-indexes.
    SOURCE_PATH  = "source_path"
    CONTENT_HASH = "content_hash"


class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"
    SCOPE   = "scope"   # added by sub-PR #6 — matches ChunkFilterField.SCOPE for unified queries


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
    # Marks which embedding model produced this package's vectors so the
    # indexing service can force re-embed when YAML's embedding.model_name
    # changes. ``None`` = pre-hybrid cache (no vectors yet).
    embedding_model: str | None = None


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
    embedding: Embedding | None = None  # spec §5.1: populated by the embed
    # stage during ingestion; stays None on read paths (vectors live in the
    # .tq sidecar, the SQL row doesn't carry them back).
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


@dataclass(frozen=True, slots=True)
class PackageDoc:
    """Groups the three query results for get_package_doc one-shot retrieval
    (spec §5.1). Consumed by :class:`PackageLookup` in the application
    layer; keeping chunks/members as tuples makes the whole value object
    hashable-ish and safe to pass across async boundaries."""
    kind: ClassVar[str] = "package_doc"
    package: Package
    chunks: tuple[Chunk, ...]
    members: tuple[ModuleMember, ...]


@dataclass(slots=True)
class IndexingStats:
    """Mutable accumulator for :meth:`ProjectIndexer.index_project`
    (spec §5.3). Deliberately NOT frozen — the service mutates these counters
    while iterating over packages. `slots=True` still guards against typos
    (e.g. ``stats.indexxed += 1``) by rejecting unknown attributes."""
    project_indexed: bool = False
    indexed: int = 0
    cached: int = 0
    failed: int = 0
