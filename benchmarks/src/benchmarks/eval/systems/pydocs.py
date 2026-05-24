"""In-process ``pydocs-mcp`` adapter (spec §4.10).

Wraps the shipped indexer + chunk-search pipeline so the runner can
benchmark them without subprocessing the MCP server. Reuses
``ProjectIndexer`` / ``build_chunk_pipeline_from_config`` /
``build_sqlite_*`` exactly as ``__main__.py`` does — only difference is
the SQLite cache lives in a tmp file per ``index()`` call so two
``EvalTask`` corpora cannot bleed into one another.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..gold_resolver import (
    _DEFAULT_FUZZ_THRESHOLD,
    LazyFuzzyGoldResolver,
    PydocsFuzzyGoldResolver,
)
from ..serialization import system_registry
from .base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline

    from ..gold_resolver import GoldResolver


@system_registry.register("pydocs-mcp")
@dataclass
class PydocsMcpSystem:
    """Index a corpus into a fresh tmp SQLite and serve queries via the
    shipped chunk pipeline.

    Mutable on purpose: ``index()`` populates per-run state
    (``_db_path``, ``_pipeline``) that ``search()`` reads back. The
    runner constructs once and calls ``index → search* → teardown`` for
    each task, so per-instance state is bounded to one task.
    """

    name: str = "pydocs-mcp"
    # WHY: cross-system output-shape parity. Context7/Neuledge each return a
    # single doc blob, so for a fair ``recall@1`` comparison pydocs must emit
    # ONE composite chunk too — not its pre-budget N-item ranked list (which
    # would hand pydocs an unfair "more text -> more chance of a fuzzy match"
    # edge). When True, ``search()`` prefers the budgeted composite
    # (``state.result``) over the ranked list (``state.candidates``). Default
    # False keeps the recall@k-friendly N-item behavior for RepoQA et al.
    composite_mode: bool = False
    _db_path: Path | None = field(default=None, init=False, repr=False)
    _pipeline: "CodeRetrieverPipeline | None" = field(
        default=None, init=False, repr=False,
    )

    async def index(self, corpus_dir: Path, config: "AppConfig") -> None:
        # WHY: imports deferred so constructing the system (which the
        # registry does on a bare ``build()``) doesn't drag in the whole
        # ``pydocs_mcp.retrieval`` chain when only ``search()`` callers
        # need it.
        from pydocs_mcp.application import ProjectIndexer
        from pydocs_mcp.db import build_connection_provider, open_index_database
        from pydocs_mcp.extraction import (
            AstMemberExtractor,
            PipelineChunkExtractor,
            StaticDependencyResolver,
            build_ingestion_pipeline,
        )
        from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config
        from pydocs_mcp.retrieval.factories import build_retrieval_context
        from pydocs_mcp.storage.factories import (
            build_sqlite_indexing_service,
            build_sqlite_uow_factory,
        )
        from pydocs_mcp.storage.sqlite import SqliteChunkRepository

        # WHY: a second ``index()`` call without an intervening ``teardown()``
        # would orphan the prior tmp SQLite (+ WAL/SHM) once ``_db_path`` is
        # overwritten. Teardown is idempotent and a no-op on first call.
        await self.teardown()

        # WHY: ``mkstemp`` returns an open fd we close immediately because
        # ``open_index_database`` will reopen the path. We own the lifecycle
        # and remove it in ``teardown``.
        fd, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._db_path = Path(name)
        open_index_database(self._db_path).close()

        uow_factory = build_sqlite_uow_factory(self._db_path)
        indexing_service = build_sqlite_indexing_service(self._db_path)

        ingestion_pipeline = build_ingestion_pipeline(config)
        # WHY: AST-only member extraction matches the safer "static" mode
        # of the CLI — no inspect-mode imports during a benchmark sweep,
        # so a malformed corpus cannot fire arbitrary code through Python
        # import side-effects.
        indexer = ProjectIndexer(
            indexing_service=indexing_service,
            dependency_resolver=StaticDependencyResolver(),
            chunk_extractor=PipelineChunkExtractor(pipeline=ingestion_pipeline),
            member_extractor=AstMemberExtractor(),
            uow_factory=uow_factory,
        )
        await indexer.index_project(
            corpus_dir,
            force=True,
            include_project_source=True,
            workers=1,
        )
        # WHY: bulk-insert path defers the FTS5 content-backed rebuild —
        # without this call ``chunks_fts MATCH ?`` returns zero rows even
        # though ``chunks`` is populated (same as ``__main__.py`` does at
        # the end of every index run).
        chunk_repo = SqliteChunkRepository(
            provider=build_connection_provider(self._db_path),
        )
        await chunk_repo.rebuild_index()

        context = build_retrieval_context(self._db_path, config)
        self._pipeline = build_chunk_pipeline_from_config(config, context)

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        if self._pipeline is None:
            raise RuntimeError(
                "PydocsMcpSystem.search called before index — runner contract",
            )
        from pydocs_mcp.models import ChunkList, SearchQuery

        state = await self._pipeline.run(
            SearchQuery(terms=query, max_results=limit),
        )
        # WHY: which state slot we read controls pydocs's output SHAPE.
        # ``state.candidates`` holds the ranked top-K (chunk_search_ranked.yaml);
        # ``state.result`` holds the budgeted 1-item composite that
        # TokenBudgetStep renders from those candidates (chunk_search.yaml),
        # leaving ``state.candidates`` untouched.
        #   composite_mode=False (default): prefer ``state.candidates`` so
        #     recall@k can measure K separate hits — collapsing to the 1-item
        #     composite would make per-K recall unmeasurable. This is the
        #     behavior RepoQA and every other caller rely on.
        #   composite_mode=True: prefer ``state.result`` so pydocs emits ONE
        #     composite chunk, matching Context7/Neuledge's single blob for a
        #     fair cross-system recall@1 (no "more text -> more fuzzy-match
        #     chances" edge from the N-item list).
        # Either branch falls back to the other slot when its preferred slot is
        # empty/None, so an adapter mis-wiring (e.g. a missing
        # token_budget_formatter step) degrades gracefully instead of returning
        # nothing.
        items_source: ChunkList | None = None
        if self.composite_mode:
            if isinstance(state.result, ChunkList) and state.result.items:
                items_source = state.result
            elif isinstance(state.candidates, ChunkList):
                items_source = state.candidates
        else:
            if isinstance(state.candidates, ChunkList) and state.candidates.items:
                items_source = state.candidates
            elif isinstance(state.result, ChunkList):
                items_source = state.result
        if items_source is None:
            return ()
        out: list[RetrievedItem] = []
        for rank, chunk in enumerate(items_source.items, start=1):
            meta = chunk.metadata
            out.append(
                RetrievedItem(
                    rank=rank,
                    text=chunk.text,
                    source_path=str(meta.get("source_path", "")),
                    qualified_name=_first_str(
                        meta.get("qualified_name"), meta.get("title"),
                    ),
                    relevance=chunk.relevance,
                    # WHY: stamp the store row id so the eager
                    # ``PydocsFuzzyGoldResolver`` (which keys store rows as
                    # ``chunk:{id}``) lines up with these ranked items. The
                    # FTS path preserves the id via ``_row_to_candidate``;
                    # composite/budgeted chunks carry ``id=None`` (-> rank
                    # key), which is why composite_mode uses the lazy
                    # resolver instead.
                    chunk_id=chunk.id,
                ),
            )
        return tuple(out)

    @property
    def gold_resolver(self) -> "GoldResolver":
        """Per-system ground-truth resolver (opt into ``HasGoldResolver``).

        WHY a property (not a field): the choice depends on
        ``composite_mode`` and on ``_db_path``, which is only set after
        ``index()``; the runner reads ``gold_resolver`` AFTER index+search.

        WHY the composite split: a composite/budgeted chunk has ``id=None``,
        so it can't be id-matched against the store — composite mode must
        match on content (lazy), exactly like Context7/Neuledge. Native
        (ranked) mode enumerates the store and id-matches (eager). The
        ``build_sqlite_uow_factory`` import is DEFERRED so ``import pydocs``
        works without ``pydocs_mcp`` installed (matching ``index()``).
        """
        if self.composite_mode or self._db_path is None:
            return LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD)
        from pydocs_mcp.storage.factories import build_sqlite_uow_factory

        return PydocsFuzzyGoldResolver(
            build_sqlite_uow_factory(self._db_path), _DEFAULT_FUZZ_THRESHOLD,
        )

    async def teardown(self) -> None:
        # Idempotent: the runner's failure path may call this twice.
        path = self._db_path
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        # WAL/SHM siblings sometimes survive if WAL mode flushed pre-close.
        for suffix in ("-wal", "-shm"):
            sib = path.with_name(path.name + suffix)
            try:
                sib.unlink()
            except FileNotFoundError:
                pass
        self._db_path = None
        self._pipeline = None


@system_registry.register("pydocs-mcp-composite")
@dataclass
class PydocsMcpCompositeSystem(PydocsMcpSystem):
    """``pydocs-mcp`` with ``composite_mode`` defaulted on.

    WHY a registered variant (not a runner kwarg): the runner builds systems
    via ``system_registry.build(name)`` with NO kwargs, so a subclass that
    flips the default is the only way ``composite_mode=True`` reaches the
    cross-system comparison run. Everything else (index/search/teardown,
    the lazy ``gold_resolver``) is inherited unchanged — the override is
    purely the two defaults below.
    """

    name: str = "pydocs-mcp-composite"
    composite_mode: bool = True


def _first_str(*candidates: object) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None
