"""IngestionPipeline — write-side mirror of ``retrieval.CodeRetrieverPipeline``.

A 7-stage pipeline composed via decorator-registered stages
(``reference_capture`` sits between chunking and flatten). A SINGLE
pipeline handles both project and dependency modes — ``FileDiscoveryStage``
and ``PackageBuildStage`` branch on :attr:`IngestionState.target_kind`.
That keeps ``__main__.py`` / ``ProjectIndexer`` from having two
near-duplicate write paths (spec §7.1).

The reference-capture stage populates :attr:`IngestionState.references` +
:attr:`IngestionState.reference_aliases` via
:class:`~pydocs_mcp.extraction.pipeline.stages.ReferenceCaptureStage`,
which runs after chunking and before flatten. The resolver pass lives
later inside ``IndexingService.reindex_package`` so it has access to the
cross-package qname universe via ``uow.trees``.

Cleanup-PR I7 (3-commit transition): :class:`IngestionState` is being
reshaped from a flat record into three value-object bundles
(:class:`FileBundle`, :class:`ChunkBundle`, :class:`ReferenceBundle`).

* Commit 1 (this commit) — introduces the three bundle types alongside
  the existing flat fields. Nothing reads/writes the bundles yet; they
  exist as default-constructed companions so commit 2 can migrate
  stages one at a time without breaking the suite.
* Commit 2 — every :class:`IngestionStage` reads/writes via the bundles
  and mirrors writes to the flat fields so this commit's external API
  stays intact through the migration window.
* Commit 3 — flat fields drop; the bundle field ``chunks_bundle`` is
  renamed to ``chunks`` (the flat-field name freed in step 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.models import Chunk, Package


class TargetKind(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"


# Cleanup-PR I7 — bundle value objects. Each holds one stage-group's
# slice of the previously flat :class:`IngestionState`. Shapes mirror
# the legacy flat fields one-to-one so commit 2's stage-by-stage
# migration is a pure field-access change rather than a wire-format
# rewrite.

@dataclass(frozen=True, slots=True)
class FileBundle:
    """Discovery + file-read outputs: where to read from, what was read.

    Wraps the (``target``, ``target_kind``, ``package_name``, ``root``,
    ``paths``, ``file_contents``, ``content_hash``) tuple populated by
    :class:`FileDiscoveryStage`, :class:`FileReadStage`, and
    :class:`ContentHashStage`. Splitting the state into bundles keeps
    stage signatures honest about the slice they touch and stops
    :class:`IngestionState` from growing into a god object.
    """

    target:        Path | str                  = field(default_factory=lambda: Path("."))
    target_kind:   TargetKind                  = TargetKind.PROJECT
    package_name:  str                         = ""
    root:          Path                        = field(default_factory=lambda: Path("."))
    paths:         tuple[str, ...]             = ()
    file_contents: tuple[tuple[str, str], ...] = ()
    content_hash:  str                         = ""


@dataclass(frozen=True, slots=True)
class ChunkBundle:
    """Chunking-stage outputs: the per-file trees + their flat chunk list.

    Populated by :class:`ChunkingStage` (``trees``) and
    :class:`FlattenStage` (``chunks``). See :class:`FileBundle` for the
    rationale behind the bundle split.
    """

    trees:  tuple["DocumentNode", ...] = ()
    chunks: tuple["Chunk", ...]        = ()


@dataclass(frozen=True, slots=True)
class ReferenceBundle:
    """Reference-capture-stage outputs: unresolved refs + alias tables.

    Populated by :class:`ReferenceCaptureStage`; consumed by the resolver
    pass inside :class:`IndexingService.reindex_package` (which has access
    to the cross-package qname universe via ``uow.trees``).

    * ``references`` — the unresolved tuple itself.
    * ``reference_aliases`` — per-module alias table; the resolver
      consumes it independently of whether IMPORTS rows survive the
      capture-config kinds filter (spec §5.3).
    * ``class_attribute_types`` — per-class ``self.X`` attribute-type
      table; used by the resolver's Rule 0 to rewrite ``self.X.Y`` →
      ``<type>.Y`` before Rule 5.
    """

    references:            tuple[Any, ...]              = ()
    reference_aliases:     dict[str, dict[str, str]]    = field(default_factory=dict)
    class_attribute_types: dict[str, dict[str, str]]    = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IngestionState:
    """Immutable state threaded through the ingestion pipeline.

    Stages return a NEW ``IngestionState`` (via ``dataclasses.replace``)
    rather than mutating in place — mirrors ``retrieval.PipelineState``
    so the same composition rules apply on the write side.

    ``target`` is either a ``Path`` (project directory) or a ``str``
    (PyPI distribution name) — the discriminator is :attr:`target_kind`,
    not Python's ``isinstance`` check, so both arms stay explicit.

    **I7 commit 1 of 3:** the three bundle fields (``files`` /
    ``chunks_bundle`` / ``refs``) default-construct alongside the legacy
    flat fields. No stage reads them yet; commit 2 migrates stages
    one-by-one with mirror writes; commit 3 drops the legacy fields and
    renames ``chunks_bundle`` → ``chunks``.
    """

    target:        Path | str
    target_kind:   TargetKind
    package_name:  str                              = ""
    root:          Path                             = field(default_factory=lambda: Path("."))
    paths:         tuple[str, ...]                  = ()
    file_contents: tuple[tuple[str, str], ...]      = ()
    trees:         tuple["DocumentNode", ...]       = ()
    chunks:        tuple["Chunk", ...]              = ()
    content_hash:  str                              = ""
    package:       "Package | None"                 = None
    # Sub-PR #5b — populated by :class:`ReferenceCaptureStage`. Kept here
    # (not in extra_metadata) so it's a typed first-class field; the
    # resolver pass in :class:`IndexingService.reindex_package` reads
    # both ``references`` and ``reference_aliases`` together.
    references:    tuple[Any, ...]                  = ()
    # Sub-PR #5b — per-module alias table captured alongside references.
    # Forwarded to the resolver inside ``IndexingService.reindex_package``;
    # carried as a dict because alias semantics are sparse + per-module and
    # don't fit a flat tuple.
    reference_aliases: dict[str, dict[str, str]]    = field(default_factory=dict)
    # Sub-PR #5d — per-class ``self.X`` attribute types captured from
    # ``__init__`` bodies. Same shape as ``reference_aliases`` but keyed
    # by class qname instead of module qname; consumed by the resolver's
    # Rule 0 to rewrite ``self.X.Y`` → ``<type>.Y`` before Rule 5.
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)
    # Hash → SQLite-id map of chunks already persisted for ``package``.
    # Populated by :class:`LoadExistingChunkHashesStage` from
    # ``uow.chunks.list_id_hash_pairs`` and consumed by
    # :class:`EmbedChunksStage` to skip embedding chunks whose
    # pipeline-aware content_hash already lives in the DB (spec Decision 5).
    # ``None`` means "stage didn't run / no factory" — distinct from ``{}``
    # which means "ran and found nothing already cached".
    existing_chunk_hashes: dict[str, int] | None = None

    # I7 commit 1 of 3 — new bundle fields, default-constructed. Stages
    # still read/write the legacy flat fields above; commit 2 migrates
    # them. The bundle named ``chunks_bundle`` collides with the legacy
    # flat field ``chunks`` only by intent — commit 3 drops the flat one
    # and renames this bundle to its final name.
    files:         FileBundle                       = field(default_factory=FileBundle)
    chunks_bundle: ChunkBundle                      = field(default_factory=ChunkBundle)
    refs:          ReferenceBundle                  = field(default_factory=ReferenceBundle)


@runtime_checkable
class IngestionStage(Protocol):
    """One stage in the ingestion pipeline.

    Receives a state, returns a state. Mirrors the ``PipelineStage``
    shape from the retrieval side (spec §7.1, §7.5).
    """

    async def run(self, state: IngestionState) -> IngestionState: ...


@dataclass(frozen=True, slots=True)
class IngestionPipeline:
    """Composes a tuple of :class:`IngestionStage`\\s left-to-right.

    No routing built in — branching on ``target_kind`` happens INSIDE
    individual stages (e.g. ``FileDiscoveryStage`` calls the project or
    dependency discoverer based on ``state.target_kind``). Keeps the
    pipeline type one-dimensional: just a linear chain of stages.
    """

    stages: tuple[IngestionStage, ...]

    async def run(self, state: IngestionState) -> IngestionState:
        for stage in self.stages:
            state = await stage.run(state)
        return state
