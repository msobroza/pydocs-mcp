"""Oracle-indexing ``pydocs-mcp`` adapter (spec §4.10, Task 8).

Where :class:`PydocsMcpSystem` runs the full ``ProjectIndexer`` / AST
extraction over a corpus on disk, ``PydocsOracleSystem`` BYPASSES all of
that and writes chunks directly from ``code-rag-bench/library-documentation``
HF dataset rows into pydocs's ``chunks`` table — one chunk per row, with the
row's ``doc_id`` preserved in the chunk's ``title`` metadata (the
``chunks.title`` column, key ``ChunkFilterField.TITLE.value``). That
``title`` round-trips through SQLite, is FTS5-indexed, and surfaces as
``RetrievedItem.qualified_name`` (the inherited ``search()`` reads
``meta.get("qualified_name") or meta.get("title")``).

This is the "oracle" mode: the corpus IS the gold documentation, so an
exact ``doc_id`` match (via :class:`PydocsOracleGoldResolver`) is the
ground truth — no fuzzy text scoring needed. The point is to measure
retrieval quality with a perfect corpus, isolating the pipeline's ranking
from any extraction noise.

``index()`` IGNORES ``corpus_dir``: DS-1000's ``corpus_source`` is
``/dev/null`` (the rows come from ``rows_source`` / HF, never from disk),
and the runner ``rmtree``s ``/dev/null`` harmlessly. Otherwise it
replicates the parent's tmp-SQLite + ``uow_factory`` + FTS-rebuild +
pipeline scaffolding verbatim so the inherited ``search()`` /
``teardown()`` work unchanged.

``rows_source`` is INJECTABLE so hermetic tests pass 5 canned rows with NO
``datasets`` import and NO network; real runs leave it ``None`` and the
deferred HF loader fires. Both ``datasets`` and ``pydocs_mcp`` imports stay
DEFERRED inside ``index()`` so this module imports without either.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..serialization import system_registry
from .pydocs import PydocsMcpSystem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from ..gold_resolver import GoldResolver


@system_registry.register("pydocs-oracle")
@dataclass
class PydocsOracleSystem(PydocsMcpSystem):
    """Index ``code-rag-bench/library-documentation`` rows straight into a
    fresh tmp SQLite (skipping ``ProjectIndexer``), then serve queries via
    the shipped chunk pipeline inherited from :class:`PydocsMcpSystem`.

    Mutable on purpose (matches the parent): ``index()`` populates
    ``_db_path`` / ``_pipeline`` that ``search()`` reads back.
    """

    name: str = "pydocs-oracle"
    # WHY: injectable dataset source. When set (tests), it returns the
    # already-materialized rows; when None (real runs), ``index()``
    # deferred-imports ``datasets`` and loads the HF corpus. Typed as a
    # zero-arg callable yielding row Mappings (each with ``doc_id`` /
    # ``doc_content`` / a library field).
    rows_source: Callable[[], Iterable[Mapping]] | None = field(default=None)

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        # WHY: imports deferred so constructing the system (which the
        # registry does on a bare ``build()``) doesn't drag in the whole
        # ``pydocs_mcp.retrieval`` chain — and so the module imports even
        # without ``pydocs_mcp`` / ``datasets`` present.
        from pydocs_mcp.db import build_connection_provider, open_index_database
        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.models import Chunk, ChunkFilterField, Package, PackageOrigin
        from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config
        from pydocs_mcp.retrieval.factories import build_retrieval_context
        from pydocs_mcp.storage.factories import build_sqlite_uow_factory
        from pydocs_mcp.storage.sqlite import SqliteChunkRepository

        # WHY: ``corpus_dir`` is deliberately UNUSED — the oracle's chunk
        # source is the library-documentation rows, not files on disk.
        # DS-1000 hands a ``/dev/null`` corpus here.
        del corpus_dir

        # WHY: a second ``index()`` without an intervening ``teardown()``
        # would orphan the prior tmp SQLite once ``_db_path`` is overwritten.
        # Teardown is idempotent and a no-op on first call.
        await self.teardown()

        # WHY: mirror the parent — ``mkstemp`` returns an open fd we close
        # immediately because ``open_index_database`` reopens the path. We
        # own the lifecycle and remove it in ``teardown``.
        fd, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._db_path = Path(name)
        open_index_database(self._db_path).close()

        uow_factory = build_sqlite_uow_factory(self._db_path)

        rows = self._load_rows()

        # Build one chunk per row. The library field varies across the HF
        # schema, so pick it defensively. The package name is normalized the
        # SAME way the resolver filters (and the same way pydocs stores
        # ``Package.name``) so the package-filtered resolver scan aligns.
        chunks: list[Chunk] = []
        libraries: set[str] = set()
        for row in rows:
            raw_library = row.get("library") or row.get("source") or ""
            library = normalize_package_name(raw_library)
            libraries.add(library)
            chunks.append(
                Chunk(
                    text=row["doc_content"],
                    metadata={
                        ChunkFilterField.PACKAGE.value: library,
                        ChunkFilterField.TITLE.value: row["doc_id"],
                        ChunkFilterField.ORIGIN.value: "oracle",
                    },
                )
            )

        # WRITE path (CLAUDE.md §"Creating new application services"): a
        # single UoW, one explicit ``commit()`` — synthetic Package per
        # distinct library, then all chunks, atomically.
        async with uow_factory() as uow:
            for library in sorted(libraries):
                await uow.packages.upsert(
                    Package(
                        name=library,
                        version="oracle",
                        summary="",
                        homepage="",
                        dependencies=(),
                        content_hash="",
                        origin=PackageOrigin.DEPENDENCY,
                    )
                )
            await uow.chunks.upsert(chunks)
            await uow.commit()

        # WHY: bulk-insert path defers the FTS5 content-backed rebuild —
        # without this call ``chunks_fts MATCH ?`` returns zero rows even
        # though ``chunks`` is populated (exactly as ``PydocsMcpSystem.index``
        # does at the end of every index run).
        chunk_repo = SqliteChunkRepository(
            provider=build_connection_provider(self._db_path),
        )
        await chunk_repo.rebuild_index()

        context = build_retrieval_context(self._db_path, config)
        self._pipeline = build_chunk_pipeline_from_config(config, context)

    def _load_rows(self) -> list[Mapping]:
        """Materialize the documentation rows.

        Injected ``rows_source`` wins (hermetic tests); otherwise the real
        HF loader fires with both ``datasets`` and the pinned revision
        imported DEFERRED so neither is required to import this module or
        run the test suite.
        """
        if self.rows_source is not None:
            return list(self.rows_source())
        # WHY (deferred): the network-touching path. ``datasets`` is only an
        # optional dep and tests never reach here. Import the pinned revision
        # from the dataset module to avoid drift between loader + indexer.
        from datasets import load_dataset

        from ..datasets.ds1000 import _PINNED_LIBDOCS_REVISION

        dataset = load_dataset(
            "code-rag-bench/library-documentation",
            revision=_PINNED_LIBDOCS_REVISION,
            split="train",
        )
        return list(dataset)

    @property
    def gold_resolver(self) -> GoldResolver:
        """Exact-match ground-truth resolver for oracle mode.

        Unlike the parent's fuzzy/composite split, the oracle always
        id-matches stored rows by ``doc_id`` (no composite blobs here), so
        this returns :class:`PydocsOracleGoldResolver` built from the
        post-index ``_db_path``. The ``pydocs_mcp`` / resolver imports are
        DEFERRED so the module imports without ``pydocs_mcp`` installed.
        """
        from pydocs_mcp.storage.factories import build_sqlite_uow_factory

        from ..gold_resolver import PydocsOracleGoldResolver

        return PydocsOracleGoldResolver(build_sqlite_uow_factory(self._db_path))
