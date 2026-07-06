"""In-process ``pydocs-mcp`` adapter (spec §4.10).

Wraps the shipped indexer + chunk-search pipeline so the runner can
benchmark them without subprocessing the MCP server. Reuses
``ProjectIndexer`` / ``build_chunk_pipeline_from_config`` /
``build_sqlite_*`` exactly as ``__main__.py`` does — only difference is
the SQLite cache lives in a tmp file per ``index()`` call so two
``EvalTask`` corpora cannot bleed into one another.
"""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .. import _bench_cache
from ..gold_resolver import (
    _DEFAULT_FUZZ_THRESHOLD,
    LazyFuzzyGoldResolver,
    PydocsFuzzyGoldResolver,
)
from ..serialization import system_registry
from .base_system import RetrievedItem

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.storage.protocols import UnitOfWork

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
    # WHY: the runner flips this per sweep via the ``IndexesDependencies``
    # opt-in (see ``base_system.py``). ``True`` indexes the corpus's declared
    # deps — reference-project datasets (DS-1000) need it. ``False`` indexes
    # repo-source-only for per-task repo datasets (RepoQA), where deps are
    # noise and the dominant ingestion cost. Default ``True`` keeps direct /
    # test construction production-shaped.
    index_dependencies: bool = True
    # WHY: preset-pinned variants set this; ``index()`` reloads AppConfig
    # from it (via ``_preset_override``) regardless of the YAML the runner
    # pinned for the sweep leg — a variant is defined BY its preset, so
    # running it against any other YAML would defeat the comparison.
    # ``None`` = use the runner's config unchanged (the base system).
    _config_path: Path | None = None
    _db_path: Path | None = field(default=None, init=False, repr=False)
    _pipeline: CodeRetrieverPipeline | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _db_is_cached: bool = field(default=False, init=False, repr=False)
    _was_cache_hit: bool = field(default=False, init=False, repr=False)

    @property
    def was_cache_hit(self) -> bool:
        """True iff the most recent index() returned a cached DB without
        indexing. The runner uses this to skip the indexing_seconds
        observation on warm tasks (a ~0 s cache lookup is not an
        indexing-time measurement)."""
        return self._was_cache_hit

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        # WHY: imports deferred so constructing the system (which the
        # registry does on a bare ``build()``) doesn't drag in the whole
        # ``pydocs_mcp.retrieval`` chain when only ``search()`` callers
        # need it.
        from pydocs_mcp.db import open_index_database

        # CRITICAL ordering: apply the preset override BEFORE
        # ``_bench_cache.make_key(corpus_dir, config)`` below. The tree
        # variants are defined BY the preset they load; keying the cache on
        # the runner's config instead would let the variants silently reuse
        # each other's indexed DBs (wrong-results bug).
        config = self._preset_override(config)

        # WHY: a second ``index()`` call without an intervening ``teardown()``
        # would orphan the prior tmp SQLite (+ WAL/SHM) once ``_db_path`` is
        # overwritten. Teardown is idempotent and a no-op on first call.
        await self.teardown()

        if _bench_cache.is_enabled():
            key = _bench_cache.make_key(corpus_dir, config)
            cached = _bench_cache.lookup(key)
            if cached is not None:
                # HIT: reuse the indexed DB (+ its sidecars) as-is.
                self._db_path = cached
                self._db_is_cached = True
                self._was_cache_hit = True
            else:
                # MISS: index into a tmp dir, then atomically promote so the
                # .tq/.plaid sidecars travel with the .sqlite.
                build_dir = _bench_cache.reserve(key)
                self._db_path = build_dir / _bench_cache._DB_FILENAME
                self._was_cache_hit = False
                # WHY (review C1): mark _db_is_cached only AFTER a successful
                # commit. If _do_index raises, teardown() would skip a
                # "cached" path and leak the half-built tmp dir, so clean
                # the build dir here and re-raise.
                self._db_is_cached = False
                open_index_database(self._db_path).close()
                try:
                    await self._do_index(corpus_dir, config)
                except BaseException:
                    shutil.rmtree(build_dir, ignore_errors=True)
                    self._db_path = None
                    raise
                self._db_path = _bench_cache.commit(key, build_dir)
                self._db_is_cached = True
        else:
            self._db_path = self._create_tmp_db()
            self._db_is_cached = False
            self._was_cache_hit = False
            await self._do_index(corpus_dir, config)

        # WHY: release the PREVIOUS needle's query-time pipeline (which holds
        # the torch dense embedder behind the dense scorer) BEFORE building the
        # new one. ``teardown()`` above already nulled ``_pipeline``; force a
        # collection here so the old embedder's CUDA memory is freed before the
        # new ingestion/query embedders allocate — otherwise device memory
        # accumulates across needles until even a tiny GPU allocation OOMs.
        # No-op for CPU embedders.
        self._pipeline = None
        gc.collect()
        self._pipeline = self._build_search_pipeline(config)

    def _preset_override(self, config: AppConfig) -> AppConfig:
        """Swap in this variant's pinned preset YAML, carrying the runner's
        ``--gpu`` device stamp through the reload (the preset dictates the
        pipeline, not the embedder execution device). Identity when no
        preset is pinned (the base system)."""
        if self._config_path is None:
            return config
        from pydocs_mcp.retrieval.config import AppConfig

        return AppConfig.load(explicit_path=self._config_path).with_device(
            gpu=(config.embedding.device == "cuda"),
        )

    def _create_tmp_db(self) -> Path:
        """A fresh empty SQLite for one uncached ``index()`` call.

        WHY: ``mkstemp`` returns an open fd we close immediately because
        ``open_index_database`` will reopen the path. We own the lifecycle
        and remove it in ``teardown``.
        """
        from pydocs_mcp.db import open_index_database

        fd, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        path = Path(name)
        open_index_database(path).close()
        return path

    def _build_write_factory(self, config: AppConfig) -> Callable[[], UnitOfWork]:
        """Composite write-path factory for the store at ``self._db_path``.

        WHY (#64): route ingestion through the SAME write path production
        uses. ``build_search_backend(...).write_uow_children()`` yields the
        SQLite + TurboQuant (+ optional fast-plaid) child UoW factories; the
        composite uow_factory makes ``uow.vectors`` a real TurboQuant store
        so dense embeddings persist to the ``.tq`` sidecar. A SQLite-only
        factory would leave ``uow.vectors`` a silent ``NullVectorStore``,
        dropping every embedding and silently degrading dense/hybrid/LI
        benchmark configs to BM25. Single canonical home for this rationale
        — the oracle subclass reuses the hook instead of restating it.
        """
        from pydocs_mcp.storage.factories import build_composite_uow_factory
        from pydocs_mcp.storage.search_backend import build_search_backend

        backend = build_search_backend(config, self._db_path)
        return build_composite_uow_factory(backend.write_uow_children())

    async def _rebuild_fts(self) -> None:
        """WHY: the bulk-insert path defers the FTS5 content-backed rebuild
        — without this call ``chunks_fts MATCH ?`` returns zero rows even
        though ``chunks`` is populated (same as ``__main__.py`` does at the
        end of every index run)."""
        from pydocs_mcp.storage.factories import build_connection_provider
        from pydocs_mcp.storage.sqlite import SqliteChunkRepository

        chunk_repo = SqliteChunkRepository(
            provider=build_connection_provider(self._db_path),
        )
        await chunk_repo.rebuild_index()

    def _build_search_pipeline(self, config: AppConfig) -> CodeRetrieverPipeline:
        from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config
        from pydocs_mcp.retrieval.factories import build_retrieval_context

        context = build_retrieval_context(self._db_path, config)
        return build_chunk_pipeline_from_config(config, context)

    async def _do_index(self, corpus_dir: Path, config: AppConfig) -> None:
        """Index ``corpus_dir`` into the SQLite at ``self._db_path`` (already
        created/empty): composite write factory → populate → FTS rebuild.
        No tmp-file or search-pipeline lifecycle here — the caller owns
        ``self._db_path`` and the pipeline."""
        uow_factory = self._build_write_factory(config)
        await self._populate(corpus_dir, config, uow_factory)
        await self._rebuild_fts()

    async def _populate(
        self,
        corpus_dir: Path,
        config: AppConfig,
        uow_factory: Callable[[], UnitOfWork],
    ) -> None:
        """Extract + embed ``corpus_dir`` into the store behind
        ``uow_factory``. Subclasses override ONLY this to swap the
        population strategy (the oracle writes HF rows directly); the
        tmp-DB / FTS-rebuild / search-pipeline lifecycle stays with the
        caller."""
        from pydocs_mcp.application import ProjectIndexer
        from pydocs_mcp.application.indexing_service import IndexingService
        from pydocs_mcp.extraction import (
            AstMemberExtractor,
            PipelineChunkExtractor,
            StaticDependencyResolver,
            build_ingestion_pipeline,
        )
        from pydocs_mcp.extraction.strategies.embedders import build_embedder

        indexing_service = IndexingService(
            uow_factory=uow_factory,
            node_scores_enabled=config.reference_graph.node_scores.enabled,
        )

        # EmbedChunksStage is wired into the shipped ingestion pipeline by
        # default; build the embedder once here so the benchmark sweep
        # actually computes vectors during ingestion. Mirrors the production
        # indexing wiring in ``__main__.py`` — both construct the embedder
        # via ``build_embedder(config.embedding)`` and build the write UoW
        # from ``build_search_backend(...).write_uow_children()``.
        embedder = build_embedder(config.embedding)
        # WHY: LoadExistingChunkHashesStage needs uow_factory to read
        # existing content_hashes; AssignChunkContentHashStage needs
        # pipeline_hash to invalidate every chunk's hash when the embedder
        # or pipeline changes. Both are passed through BuildContext to the
        # stage from_dict methods.
        ingestion_pipeline = build_ingestion_pipeline(
            config,
            embedder=embedder,
            uow_factory=uow_factory,
            pipeline_hash=config.compute_ingestion_pipeline_hash(),
        )
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
            include_dependencies=self.index_dependencies,
            workers=1,
        )

        # WHY: release this needle's INGESTION embedder before the next
        # needle builds its own. The torch (sentence_transformers) embedder
        # holds CUDA memory that only frees when the model is dropped + the
        # cache emptied; without an explicit close + collect, every needle
        # leaks device memory and the GPU OOMs partway through a sweep.
        # Guard on ``hasattr(..., "close")`` so this is a strict no-op for
        # FastEmbed / OpenAI / PyLate embedders.
        if hasattr(embedder, "close"):
            embedder.close()
        del embedder
        del ingestion_pipeline
        gc.collect()

    async def search(
        self,
        query: str,
        limit: int,
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
                        meta.get("qualified_name"),
                        meta.get("title"),
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
    def gold_resolver(self) -> GoldResolver:
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
            build_sqlite_uow_factory(self._db_path),
            _DEFAULT_FUZZ_THRESHOLD,
        )

    async def teardown(self) -> None:
        # Idempotent: the runner's failure path may call this twice.
        path = self._db_path
        if path is None:
            return
        # WHY: a cached DB is shared across tasks/instances — deleting it on
        # teardown would defeat the cache (and corrupt a concurrent reader).
        # Only the unique tmp DB this instance owns gets unlinked here.
        if not self._db_is_cached:
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
        # WHY: dropping the pipeline releases the query-time torch embedder it
        # holds; the collection forces torch to free the model's CUDA memory so
        # a torn-down leg leaves no GPU memory behind. No-op for CPU embedders.
        self._pipeline = None
        self._db_is_cached = False
        gc.collect()


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


def _pipelines_dir() -> Path:
    """Path to the shipped ``pipelines/`` directory inside ``pydocs_mcp``.

    WHY ``importlib.resources``: resolves the shipped YAML directory regardless
    of how ``pydocs_mcp`` was installed (editable vs wheel vs zip), so the
    tree-reasoning variants below can reach ``tree_only.yaml`` /
    ``chunk_search_with_tree_reasoning_parallel.yaml`` without hard-coding a
    repo-layout path.
    """
    from importlib import resources

    return Path(str(resources.files("pydocs_mcp.pipelines")))


@system_registry.register("pydocs-mcp-tree-only")
@dataclass
class PydocsTreeOnlySystem(PydocsMcpSystem):
    """``pydocs-mcp`` vectorless variant — ``LlmTreeReasoningStep`` only.

    Loads the shipped ``tree_only.yaml`` preset (no BM25, no dense, no RRF
    fusion — purely the LLM-driven DocumentNode-tree walk) regardless of the
    YAML the runner pinned for the sweep leg. Everything else (index /
    search / teardown / gold_resolver) is inherited from ``PydocsMcpSystem``
    — the pinned ``_config_path`` routes through the parent's
    ``_preset_override`` hook.

    WHY override the runner's config: the runner pairs ``(system × cfg_path)``
    cartesianly, but this variant is defined BY the preset it loads; running
    it against any other YAML would defeat the comparison.
    """

    name: str = "pydocs-mcp-tree-only"
    _config_path: Path = field(
        default_factory=lambda: _pipelines_dir() / "tree_only.yaml",
    )


@system_registry.register("pydocs-mcp-tree-parallel")
@dataclass
class PydocsTreeParallelSystem(PydocsMcpSystem):
    """``pydocs-mcp`` hybrid + tree-reasoning in parallel, fused via RRF.

    Loads the shipped ``chunk_search_with_tree_reasoning_parallel.yaml``
    preset (BM25 + dense + LlmTreeReasoningStep run as parallel legs,
    candidate sets fused by RRF). Same override pattern as
    ``PydocsTreeOnlySystem`` — see that class for the rationale.
    """

    name: str = "pydocs-mcp-tree-parallel"
    _config_path: Path = field(
        default_factory=lambda: _pipelines_dir() / "chunk_search_with_tree_reasoning_parallel.yaml",
    )


def _first_str(*candidates: object) -> str | None:
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None
