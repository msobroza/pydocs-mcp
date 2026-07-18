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
and the runner ``rmtree``s ``/dev/null`` harmlessly. Otherwise it reuses
the parent's ``_create_tmp_db`` / ``_build_write_factory`` /
``_rebuild_fts`` / ``_build_search_pipeline`` hooks, overriding only
``_populate``, so the inherited ``search()`` / ``teardown()`` work
unchanged.

``rows_source`` is INJECTABLE so hermetic tests pass 5 canned rows with NO
``datasets`` import and NO network; real runs leave it ``None`` and the
deferred HF loader fires. Both ``datasets`` and ``pydocs_mcp`` imports stay
DEFERRED inside ``index()`` so this module imports without either.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .._retrieval_extra import require_retrieval_extra
from ..datasets.ds1000_schema import PINNED_LIBDOCS_REVISION, to_pypi_canonical
from ..registries import system_registry
from .pydocs import PydocsMcpSystem

if TYPE_CHECKING:
    from pydocs_mcp.extraction.strategies.embedders import Embedder
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.protocols import UnitOfWork

    from ..gold_resolver import GoldResolver


# WHY: the real ``code-rag-bench/library-documentation`` rows carry ONLY
# ``{doc_id, doc_content}`` — no ``library`` / ``source`` field. The
# ``doc_id`` IS the library source: dot-separated with the library as the
# first segment (``numpy.reference.arrays.scalars`` -> ``numpy``,
# ``tensorflow.aggregationmethod`` -> ``tensorflow``). The first dot-segment,
# lowercased, recovers the library when no explicit field is present.
def _library_from_doc_id(doc_id: str) -> str:
    """Recover a library name from a ``library-documentation`` ``doc_id``.

    The ``doc_id`` is dot-separated with the library as the first segment;
    return that segment lowercased. A missing / empty ``doc_id`` yields
    ``""`` so the caller stays total (never raises)."""
    return doc_id.split(".", 1)[0].strip().lower()


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
    # ``doc_content``; the library is derived from the ``doc_id`` prefix
    # when no explicit ``library`` / ``source`` field is present).
    rows_source: Callable[[], Iterable[Mapping]] | None = field(default=None)
    # WHY: injectable embedder. When set (tests), it returns deterministic
    # offline vectors so the suite skips the FastEmbed model download and
    # never touches the network; when None (real runs), ``index()`` builds
    # the configured embedder via ``build_embedder(config.embedding)``,
    # exactly as production indexing does. Typed as the ``Embedder`` Protocol
    # under TYPE_CHECKING so this module imports without ``pydocs_mcp``.
    embedder: Embedder | None = field(default=None)

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        # WHY: ``corpus_dir`` is deliberately UNUSED — the oracle's chunk
        # source is the library-documentation rows, not files on disk
        # (DS-1000 hands a ``/dev/null`` corpus here). The oracle also
        # deliberately BYPASSES ``_bench_cache``, unlike the parent: its
        # rows come from HF / injected sources, so a corpus-dir cache key
        # could not distinguish two oracle corpora.
        # ``[retrieval]`` extra boundary: this override does NOT call
        # ``super().index()`` but reaches the same library-coupled helpers
        # (``_create_tmp_db`` / ``_build_write_factory`` / ``_rebuild_fts`` /
        # ``_build_search_pipeline``) and ``_populate``. Guard here so a base
        # install without the extra gets the actionable install hint instead
        # of a bare ModuleNotFoundError.
        require_retrieval_extra()
        del corpus_dir
        await self.teardown()
        self._db_path = self._create_tmp_db()
        uow_factory = self._build_write_factory(config)
        await self._populate(Path(os.devnull), config, uow_factory)
        await self._rebuild_fts()
        self._pipeline = self._build_search_pipeline(config)

    async def _populate(
        self,
        corpus_dir: Path,
        config: AppConfig,
        uow_factory: Callable[[], UnitOfWork],
    ) -> None:
        """Bypass extraction: write ``library-documentation`` rows straight
        into the store behind ``uow_factory`` — one chunk per row, plus the
        dense vectors ``EmbedChunksStage`` would have produced."""
        # WHY: imports deferred so the module imports without ``pydocs_mcp``
        # / ``datasets`` present (hermetic construction via the registry).
        from dataclasses import replace

        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.extraction.strategies.embedders import build_embedder
        from pydocs_mcp.models import (
            Chunk,
            ChunkFilterField,
            Embedding,
            Package,
            PackageOrigin,
        )

        del corpus_dir  # rows come from rows_source / HF, never from disk

        # Resolve the embedder once. The injected fake wins (hermetic
        # tests); real runs build the configured embedder, matching
        # production wiring.
        embedder = self.embedder or build_embedder(config.embedding)

        rows = self._load_rows()

        # Build one chunk per row. The library field varies across the HF
        # schema, so pick it defensively. The package name is normalized the
        # SAME way the resolver filters (and the same way pydocs stores
        # ``Package.name``) so the package-filtered resolver scan aligns.
        chunks: list[Chunk] = []
        libraries: set[str] = set()
        for row in rows:
            # WHY: prefer an explicit ``library`` / ``source`` field (hermetic
            # fixtures supply one), else DERIVE the library from the ``doc_id``
            # prefix — the real HF corpus has neither field. Canonicalize the
            # recovered name via the SAME to_pypi_canonical the DS-1000 loader
            # uses, so the oracle's package matches the task's PyPI-canonical
            # library that the resolver filters on.
            raw_library = (
                row.get("library")
                or row.get("source")
                or _library_from_doc_id(row.get("doc_id", ""))
            )
            pypi = to_pypi_canonical(raw_library)
            library = normalize_package_name(pypi)
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

        # Embed every chunk in ``batch_size``-sized slices, stamping each
        # chunk's vector via ``dataclasses.replace`` so the order stays 1:1
        # with ``chunks``. The bypass-extraction oracle has no ingestion
        # pipeline, so it does the work ``EmbedChunksStage`` does for
        # ``PydocsMcpSystem`` (see ``extraction/pipeline/stages/embed_chunks``)
        # inline here.
        embeddings: list[Embedding] = []
        for i in range(0, len(chunks), config.embedding.batch_size):
            batch = chunks[i : i + config.embedding.batch_size]
            embeddings.extend(await embedder.embed_chunks(tuple(c.text for c in batch)))
        chunks = [replace(c, embedding=emb) for c, emb in zip(chunks, embeddings, strict=True)]

        # WRITE path (CLAUDE.md §"Creating new application services"): a
        # single UoW, one explicit ``commit()`` — synthetic Package per
        # distinct library, then all chunks + their dense vectors, atomically.
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

            # Persist the dense vectors keyed by the PERSISTED chunk id.
            # Correlate the in-memory embedded chunks to their stored rows by
            # ``title`` (the ``doc_id`` is globally unique in the corpus, so it
            # avoids the content-hash collision class ``_maybe_write_vectors``
            # has to defend against). ``uow.vectors`` is a real TurboQuant store
            # here (composite backend), so this writes the ``.tq`` sidecar.
            persisted = await uow.chunks.list(filter=None)
            title_to_id = {c.metadata.get(ChunkFilterField.TITLE.value): c.id for c in persisted}
            ids: list[int] = []
            embs: list[Embedding] = []
            for chunk in chunks:
                # Defensive: skip rows that failed to persist or carry no vector.
                chunk_id = title_to_id.get(chunk.metadata[ChunkFilterField.TITLE.value])
                if chunk_id is None or chunk.embedding is None:
                    continue
                ids.append(chunk_id)
                embs.append(chunk.embedding)
            if ids:
                await uow.vectors.add_vectors(ids, embs)

            await uow.commit()

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
        # optional dep and tests never reach here. The pinned revision is
        # shared via ``ds1000_schema`` (single source, no loader/indexer
        # drift).
        from datasets import load_dataset

        dataset = load_dataset(
            "code-rag-bench/library-documentation",
            revision=PINNED_LIBDOCS_REVISION,
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
