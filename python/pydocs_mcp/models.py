"""Canonical domain models for pydocs-mcp.

This module is the single source of truth for the domain vocabulary — see
docs/superpowers/specs/2026-04-19-sub-pr-1-naming-and-models-design.md §5.

All dataclasses are frozen + slotted. All enums subclass enum.StrEnum so values
round-trip through SQLite TEXT columns and JSON without glue code.

Filter-tree value objects + ``format_registry`` live in
:mod:`pydocs_mcp.filters` (post-PR-C Task 20 / S32). The dependency
direction is one-way: ``models → pydocs_mcp.filters``. Because the new
filters module has no internal imports, ``models`` can reach it at
module load — no lazy-import workaround needed.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Protocol, runtime_checkable

import numpy as np
from pydantic import ConfigDict, field_validator, model_validator
from pydantic.dataclasses import dataclass as pyd_dataclass

from pydocs_mcp.filters import MetadataFilterFormat, format_registry

# S5: single source of truth for the special package name that identifies
# project-source chunks/members/trees inside the indexer. Every call
# site (extraction stages, retrieval filters, server.py, tests) reaches
# the literal through this constant.
PROJECT_PACKAGE_NAME = "__project__"

# ── Embedding types (spec §5.1) ──────────────────────────────────────────
# Aligned with FastEmbed (https://github.com/qdrant/fastembed):
#
#   np.ndarray   = 1D, shape (dim,), dtype=float32.
#                  What TextEmbedding.embed() yields per document; what
#                  OpenAI returns; what TurboQuant IdMapIndex consumes.
#                  Spec S12: we no longer expose a ``Vector`` alias — call
#                  sites use ``np.ndarray`` directly so the type-checker
#                  and IDE tooling agree on the canonical name.
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
MultiVector = list[np.ndarray]
Embedding = np.ndarray | MultiVector


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


# ``MetadataFilterFormat`` is re-exported from :mod:`pydocs_mcp.filters` at
# the top of this module so ``from pydocs_mcp.models import
# MetadataFilterFormat`` keeps working. The canonical definition lives in
# ``pydocs_mcp/filters.py``; there is exactly one enum class.


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
class EmbeddingProvenance:
    """Pairs the embedding model identity with the package content hash
    that produced its vectors (S28).

    These two facts always move together: re-embed is needed iff *either*
    the model identity changed *or* the source files changed. Grouping
    them into one value object keeps that invariant visible in the type
    system. Construction is additive — Package accepts ``provenance`` as
    an optional field alongside the legacy ``embedding_model`` /
    ``content_hash`` fields, which existing callers still set directly.
    """
    model_name: str
    content_hash: str


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
    # S28: optional grouped form of (embedding_model, content_hash). Kept
    # additive so existing Package(...) callers don't need to be migrated
    # in lock-step; future readers may prefer the grouped accessor when
    # both fields are required together.
    provenance: "EmbeddingProvenance | None" = None


def compute_chunk_content_hash(
    package: str, module: str, title: str, text: str,
    pipeline_hash: str = "",
) -> str:
    """SHA-256 hex digest of the null-separated chunk-identity tuple.

    Mirrors Package.content_hash. Used by Chunk.__post_init__ for auto-
    compute (pipeline_hash="" — test ergonomics), by
    AssignChunkContentHashStage in production (pipeline_hash from
    BuildContext), and by the diff-merge in
    IndexingService.reindex_package to match incoming chunks against
    the existing SQLite snapshot.

    The pipeline_hash slot ensures embedder swaps or ingestion YAML
    edits invalidate every chunk's hash so the diff naturally
    re-embeds via the existing add path.
    """
    return hashlib.sha256(
        f"{package}\0{module}\0{title}\0{text}\0{pipeline_hash}"
        .encode("utf-8"),
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RetrievalEnrichment:
    """Retrieval-time metadata attached to a Chunk by a scoring step (S17).

    ``relevance`` is the score the named ``retriever_name`` assigned;
    grouping the two as one value object makes "which scorer produced
    this score" inseparable in the type system. Attach via
    :meth:`Chunk.with_enrichment` — Chunk treats the field as additive
    next to the legacy flat ``relevance`` / ``retriever_name`` fields so
    existing retrieval steps keep working unchanged.
    """
    relevance: float
    retriever_name: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """Unit of retrieval. `text` is the primary payload; everything else
    (package, title, origin, module) lives in metadata keyed by
    ChunkFilterField.*.value. Composite chunks (formatter output) set
    metadata['origin'] == ChunkOrigin.COMPOSITE_OUTPUT.value.

    Retrieval-time fields (relevance, retriever_name) are None until a
    retriever populates them. The same information is exposed in a
    grouped form via :attr:`enrichment` (see S17)."""
    kind: ClassVar[str] = "chunk"
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    # spec §5.1: populated by the embed stage during ingestion; stays
    # ``None`` on read paths because dense vectors live in the ``.tq``
    # sidecar and the SQL row does not carry them back into Chunk (S13).
    embedding: Embedding | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # SHA-256(package + \0 + module + \0 + title + \0 + text + \0 + pipeline_hash).
    # Production callers supply this via AssignChunkContentHashStage in
    # the ingestion pipeline OR by reading the persisted column out of
    # SQLite. The legacy auto-compute fallback (when empty) is kept for
    # backward compatibility with the handful of retrieval steps that
    # rebuild a Chunk without re-supplying the hash; the canonical
    # auto-hash entry point for tests is ``Chunk.from_test_inputs(...)``
    # (S2/S25).
    content_hash: str = ""
    # S17: optional grouped form of (relevance, retriever_name). Default
    # is ``None`` because most paths still populate the flat fields
    # directly; the grouped form is opt-in via with_enrichment().
    enrichment: "RetrievalEnrichment | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if not self.content_hash:
            # Backward-compat fallback for callers that construct a Chunk
            # without re-supplying the hash (e.g. ``token_budget`` composite
            # output, ``bm25_scorer`` re-wrapping). New test code should
            # prefer ``Chunk.from_test_inputs(...)``.
            object.__setattr__(
                self, "content_hash",
                compute_chunk_content_hash(
                    package=str(self.metadata.get("package", "")),
                    module=str(self.metadata.get("module", "")),
                    title=str(self.metadata.get("title", "")),
                    text=self.text,
                ),
            )

    @classmethod
    def from_test_inputs(
        cls,
        *,
        package: str = "",
        module: str = "",
        title: str = "",
        text: str = "",
        pipeline_hash: str = "",
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> "Chunk":
        """Test-only factory that builds a Chunk with an auto-computed
        ``content_hash`` derived from the supplied identity tuple
        (S2/S25).

        Production callers should pass ``content_hash`` explicitly via
        the regular :class:`Chunk` constructor — typically populated by
        ``AssignChunkContentHashStage`` in the ingestion pipeline or by
        reading the persisted column out of SQLite. Routing the
        auto-compute through this factory keeps the hashing rule
        addressable in tests without spreading it across production
        construction sites.

        The supplied ``package`` / ``module`` / ``title`` are written
        into the chunk metadata so the resulting Chunk filters / sorts
        like one produced by the real ingestion path.
        """
        # Start from the caller's metadata mapping (so test sites that
        # already pre-populate metadata still get their keys honored)
        # and fold in the structured identity fields for the ones that
        # are non-empty.
        merged: dict[str, Any] = dict(metadata or {})
        if package:
            merged.setdefault(ChunkFilterField.PACKAGE.value, package)
        if module:
            merged.setdefault(ChunkFilterField.MODULE.value, module)
        if title:
            merged.setdefault(ChunkFilterField.TITLE.value, title)

        return cls(
            text=text,
            metadata=merged,
            content_hash=compute_chunk_content_hash(
                package=package,
                module=module,
                title=title,
                text=text,
                pipeline_hash=pipeline_hash,
            ),
            **kwargs,
        )

    def with_enrichment(self, enrichment: "RetrievalEnrichment") -> "Chunk":
        """Return a copy of this Chunk with the supplied retrieval-time
        enrichment attached. Non-mutating — the original Chunk is left
        untouched (S17).
        """
        return replace(self, enrichment=enrichment)


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
    :data:`pydocs_mcp.filters.format_registry` (spec §5.5, AC #12).
    The dependency direction is one-way: ``models → pydocs_mcp.filters``
    (the top-level ``pydocs_mcp.filters`` module has no internal
    imports, so the registry is reachable at module load — no
    lazy-import workaround needed).
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


# Backward-compatibility shim for ``from pydocs_mcp.models import IndexingStats``.
# The canonical home is :class:`pydocs_mcp.application.indexing_service.IndexingStats`.
# Using PEP 562 ``__getattr__`` keeps the import lazy so models.py stays a leaf
# in the import graph (no edge to application, which would close a cycle through
# storage.filters → storage.protocols → models → application → retrieval →
# storage.protocols). The shim resolves on first attribute access from outside
# the module; existing ``from pydocs_mcp.models import IndexingStats`` callers
# see no behavior change.
def __getattr__(name: str):
    if name == "IndexingStats":
        from pydocs_mcp.application.indexing_service import IndexingStats
        return IndexingStats
    raise AttributeError(f"module 'pydocs_mcp.models' has no attribute {name!r}")
