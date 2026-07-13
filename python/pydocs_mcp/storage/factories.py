"""Canonical factories for the indexing + lookup services — including the
write-side composition root (``build_project_indexer`` → ``IndexerBundle``).

The MCP CLI (``__main__.py``), the MCP server (``server.py``), the
benchmark suite, and the test suite all construct services around a
shared ``ConnectionProvider`` + ``SqliteUnitOfWork`` factory. Keeping the
composition in one place means a change to the backend dependencies
(e.g. swapping in a different ``UnitOfWork`` implementation) fans out
through a single factory instead of N copies.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pydocs_mcp.application.freshness import IndexFreshnessProbe, resolve_git_head
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.overview_aggregates import (
    ActivitySummary,
    OverviewAggregates,
    OverviewSummary,
    activity_from_json,
    activity_to_json,
    compute_activity,
    generate_overview_summary,
    summary_from_json,
    summary_to_json,
)
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.protocols import ConnectionProvider, LlmClient
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    read_overview_aggregates,
    update_overview_aggregates,
    write_index_metadata,
)
from pydocs_mcp.storage.sqlite import (
    CHUNK_COLUMNS,
    SqliteChunkRepository,
    SqliteUnitOfWork,
    row_to_chunk,
)
from pydocs_mcp.storage.sqlite.filter_adapter import _SqliteFilterTranslator
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

logger = logging.getLogger(__name__)

# (module_qnames, central_symbols, cached_summary) — the inputs the index-end
# LLM architecture-summary generator needs (block 2). Named so the writer's
# ``_read_summary_inputs`` signature stays legible.
_SummaryInputs = tuple[tuple[str, ...], tuple[str, ...], OverviewSummary | None]

if TYPE_CHECKING:
    from pydocs_mcp.application.decision_service import DecisionService
    from pydocs_mcp.application.docs_search import DocsSearch
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.overview_service import OverviewService
    from pydocs_mcp.application.project_indexer import ProjectIndexer
    from pydocs_mcp.application.symbol_source import SymbolSourceService
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.sqlite.cross_link_store import SqliteCrossLinkStore


def build_connection_provider(cache_path: Path) -> PerCallConnectionProvider:
    """Factory — the default ``ConnectionProvider`` for a given DB path.

    WHY here: this is composition-root wiring. It used to live at the
    bottom of ``db.py`` behind a ``# noqa: E402`` retrieval import, which
    inverted the layering (low-level db importing retrieval).
    """
    return PerCallConnectionProvider(cache_path=cache_path)


def build_sqlite_uow_factory(
    db_path: Path,
    *,
    provider: ConnectionProvider | None = None,
) -> Callable[[], SqliteUnitOfWork]:
    """Build a fresh-per-call ``SqliteUnitOfWork`` factory bound to a single
    ``ConnectionProvider``.

    Each call to the returned callable instantiates a NEW ``SqliteUnitOfWork``
    — instances are not reusable (the re-entrance guard fires). The provider
    is captured by closure once at factory-construction time so all UoWs
    share the same connection-pool semantics.

    ``provider`` lets a caller inject a pre-built provider so the SAME
    instance can be shared with a sibling UoW (e.g. the fast-plaid child in
    a composite write set must resolve the same ``_sqlite_transaction``
    ambient connection — see ``SqliteCompositeBackend.write_uow_children``).
    When omitted, one is built from ``db_path``.
    """
    provider = provider if provider is not None else build_connection_provider(db_path)
    return lambda: SqliteUnitOfWork(provider=provider)


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    """Test / composition convenience: wrap a SQLite-only ``uow_factory`` into
    an ``IndexingService``.

    ``IndexingService`` depends on a single ``uow_factory`` callable: each
    public-method call opens a fresh UoW, runs its write sequence, and commits.
    Production wires ``IndexingService`` the same way, but sources its
    ``uow_factory`` from ``build_search_backend(...).write_uow_children()``
    (``storage/search_backend.py``) so dense (and optional fast-plaid) write
    participation is included — this helper is the SQLite-only subset, handy
    for tests and lightweight composition.
    """
    return IndexingService(uow_factory=build_sqlite_uow_factory(db_path))


def build_sqlite_lookup_service(
    db_path: Path,
    config: AppConfig | None = None,
) -> LookupService:
    """Compose a wired LookupService from a SQLite DB path.

    Post-#5a-2: ``PackageLookup``, ``TreeService``, and ``ReferenceService``
    each depend on a ``uow_factory``. We build ONE factory and thread it
    through all three so they share connection-pool semantics. Post-#5c
    (Task 8): ``ref_svc`` is now a real ``ReferenceService`` instead of
    ``None`` — ``lookup(target=X, show="callers"|"callees")`` resolves
    end-to-end through the reference graph.
    """
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.application.tree_service import TreeService
    from pydocs_mcp.retrieval.config import ContextConfig, ImpactConfig

    uow_factory = build_sqlite_uow_factory(db_path)
    package_lookup = PackageLookup(uow_factory=uow_factory)
    tree_svc = TreeService(uow_factory=uow_factory)
    ref_svc = ReferenceService(uow_factory=uow_factory)
    # ``show="impact"`` / ``show="context"`` tunables are YAML settings, not MCP
    # params; thread them from config (falling back to the shipped sub-config
    # defaults for direct/test construction with no config).
    rg = config.reference_graph if config is not None else None
    impact_cfg = rg.impact if rg is not None else ImpactConfig()
    context_cfg = rg.context if rg is not None else ContextConfig()
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
        impact_max_depth=impact_cfg.max_depth,
        context_max_depth=context_cfg.max_depth,
        context_token_budget=context_cfg.token_budget,
        context_render=context_cfg.render,
        context_body_ratio=context_cfg.skeleton_body_ratio,
    )


def build_sqlite_symbol_source_service(
    db_path: Path,
    config: AppConfig | None = None,
) -> SymbolSourceService:
    """Compose a wired ``SymbolSourceService`` from a SQLite DB path.

    Sibling of ``build_sqlite_lookup_service``: the CLI and the MCP server
    both build get_symbol(depth="source")'s backing service here so they
    never drift on the ``max_lines`` line cap. The cap is a YAML setting
    (``symbol_source.max_lines``), NOT an MCP param — thread it from
    ``config`` when given, else fall back to the module-level
    ``mcp_inputs._SYMBOL_SOURCE_MAX_LINES`` slot (populated by
    ``configure_from_app_config`` at startup, or its shipped-default literal
    for direct/test construction with no config).
    """
    from pydocs_mcp.application import mcp_inputs
    from pydocs_mcp.application.symbol_source import SymbolSourceService

    max_lines = (
        config.symbol_source.max_lines
        if config is not None
        else mcp_inputs._SYMBOL_SOURCE_MAX_LINES
    )
    return SymbolSourceService(
        uow_factory=build_sqlite_uow_factory(db_path),
        max_lines=max_lines,
    )


def build_sqlite_overview_service(
    db_path: Path,
    *,
    project_root: Path,
    config: AppConfig | None = None,
) -> OverviewService:
    """Compose a wired ``OverviewService`` from a SQLite DB path.

    Sibling of ``build_sqlite_lookup_service`` / ``build_sqlite_symbol_source_service``:
    the CLI and the MCP server both build ``get_overview``'s backing service
    here so they never drift on the card caps or on where the entry-point
    ``[project.scripts]`` come from. Caps are YAML settings (``overview.*``),
    NOT MCP params — threaded from ``config`` when given, else the sub-config
    defaults. ``scripts`` is parsed ONCE at composition from
    ``project_root/pyproject.toml`` (missing / malformed → ``{}``: entry points
    are advisory card content, never a reason to fail an overview).
    """
    from pydocs_mcp.application.overview_service import OverviewService
    from pydocs_mcp.deps import parse_project_scripts
    from pydocs_mcp.retrieval.config import OverviewConfig

    overview_cfg = config.overview if config is not None else OverviewConfig()
    return OverviewService(
        uow_factory=build_sqlite_uow_factory(db_path),
        scripts=parse_project_scripts(str(project_root / "pyproject.toml")),
        max_modules=overview_cfg.max_modules,
        max_communities=overview_cfg.max_communities,
        aggregates_reader=build_overview_aggregates_reader(db_path),
    )


def build_overview_aggregates_reader(
    db_path: Path,
) -> Callable[[], OverviewAggregates]:
    """Build the sync reader closure that hydrates the persisted overview aggregates.

    Mirrors the freshness-probe pattern (``build_freshness_probe``): a sync
    sqlite3 reader over the ``index_metadata`` JSON columns (block 9 activity
    today, block 2 LLM summary later). Each stored column deserialises
    independently; a missing / malformed value degrades to ``None`` (the block is
    omitted) rather than failing the whole overview. ``OverviewService._read_aggregates``
    invokes this sync closure via ``asyncio.to_thread``, so the blocking sqlite3
    read runs off the event loop (CLAUDE.md Async Patterns).
    """

    def _read() -> OverviewAggregates:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            activity_json, overview_json = read_overview_aggregates(conn)
        except sqlite3.Error:
            # A pre-v14 db (no columns) or a transient read error → empty
            # aggregates; the overview still renders its structural blocks.
            return OverviewAggregates()
        finally:
            conn.close()
        # Each column deserialises independently — a malformed activity JSON must
        # not drop the LLM summary block (or vice-versa).
        return OverviewAggregates(
            activity=activity_from_json(activity_json or ""),
            summary=summary_from_json(overview_json or ""),
        )

    return _read


def build_sqlite_decision_service(
    db_path: Path,
    *,
    docs: DocsSearch,
    config: AppConfig | None = None,
) -> DecisionService:
    """Compose a wired ``DecisionService`` from a SQLite DB path + shared ``docs``.

    Sibling of ``build_sqlite_lookup_service`` / ``build_sqlite_overview_service``:
    the composition root builds ``get_why``'s real backing here so the swap on
    ``decision_capture.enabled`` (server ``_build_project_services``) is one
    branch. ``docs`` is the SAME per-project :class:`DocsSearch` the search /
    card tools use — passed in rather than rebuilt so decision ranking and plain
    search share ONE semantic-retrieval pipeline (no second chunk pipeline). The
    read-record cap is a YAML setting (``decisions.output.default_limit``), NOT
    an MCP param — threaded from ``config`` when given, else the sub-config
    default for direct/test construction.
    """
    from pydocs_mcp.application.decision_service import DecisionService
    from pydocs_mcp.retrieval.config import DecisionsConfig

    decisions_cfg = config.decisions if config is not None else DecisionsConfig()
    return DecisionService(
        uow_factory=build_sqlite_uow_factory(db_path),
        docs=docs,
        default_limit=decisions_cfg.output.default_limit,
    )


def build_composite_uow_factory(
    children: Sequence[Callable[[], object]],
) -> Callable[[], CompositeUnitOfWork]:
    """Wrap N child UoW factories into a composite factory (spec §5.7).

    The returned callable instantiates each child via its factory and
    wraps them in a CompositeUnitOfWork. Order-preserving (children[0]
    commits first; rollback walks in reverse).
    """

    def _make() -> CompositeUnitOfWork:
        return CompositeUnitOfWork(*(f() for f in children))

    return _make


def build_sqlite_plus_turboquant_uow_factory(
    *,
    db_path: Path,
    tq_path: Path,
    dim: int,
    bit_width: int = 4,
) -> Callable[[], CompositeUnitOfWork]:
    """Convenience factory wiring a SQLite + TurboQuant composite.

    Used by the test suite (and available for composition) to assemble a
    SQLite + TurboQuant ``CompositeUnitOfWork`` in one call. Production
    indexing does NOT route through here: it sources its write children from
    ``SearchBackend.write_uow_children()`` (``storage/search_backend.py``),
    which also wires the optional fast-plaid child when late interaction is on.
    """
    sqlite_factory = build_sqlite_uow_factory(db_path)
    tq_factory = lambda: TurboQuantUnitOfWork(  # noqa: E731
        index_path=tq_path,
        dim=dim,
        bit_width=bit_width,
    )
    return build_composite_uow_factory([sqlite_factory, tq_factory])


def build_sqlite_candidate_id_resolver(
    db_path: Path,
) -> Callable[[Filter], Awaitable[np.ndarray]]:
    """Build a CandidateIdResolver — runs the filter as SQL against the
    SQLite cache and returns matching chunk IDs as ``np.uint64``.

    Used by ``TurboQuantVectorStore`` to construct its allowlist (spec §7
    risk row 1). The vector store does not import sqlite3 directly; this
    callable is the only seam through which it learns about the relational
    cache, so a future Qdrant / Postgres adapter slots its own resolver in
    without touching the store class.
    """
    provider = build_connection_provider(db_path)
    adapter = _SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS)

    async def resolve(filter_tree: Filter) -> np.ndarray:
        sql_clause, params = adapter.adapt(filter_tree)
        sql = f"SELECT id FROM chunks WHERE {sql_clause}"
        async with _maybe_acquire(provider) as conn:
            rows = await asyncio.to_thread(lambda: conn.execute(sql, params).fetchall())
        # ``np.asarray([], dtype=np.uint64)`` preserves the dtype on the
        # empty-result path; numpy would otherwise infer float64 from [].
        return np.asarray([r[0] for r in rows], dtype=np.uint64)

    return resolve


async def check_integrity_and_repair(
    *,
    db_path: Path,
    tq_path: Path,
    dim: int,
    bit_width: int,
) -> list[str]:
    """Compare INTENDED embeddings (``chunks.embedded = 1``) vs TurboQuant
    ``size()``; repair drift.

    Composite SQLite + TurboQuant deployments are not strictly cross-backend
    ACID (see :class:`CompositeUnitOfWork` docstring). A crash between the
    SQLite commit and the TurboQuant ``.tq`` write can leave the two
    backends out of sync. This startup hook detects the drift by
    counting both sides; on mismatch it logs a warning and clears
    ``packages.content_hash`` on every package so the next indexing sweep
    treats them as stale and re-extracts (re-embedding the chunks in the
    process). Returns the list of repaired package names so callers can
    surface them in logs / metrics.

    The fresh-project case is intentional: when neither backend has any
    rows yet (``embedded_count == 0 == vec_count``) the function is a no-op
    and returns ``[]``. ``TurboQuantUnitOfWork.__aenter__`` synthesises an
    empty in-memory index for a missing ``.tq`` file, so ``size() == 0``
    matches an empty flag count — no false alarm. Per spec §5.7
    (cache is regenerable; silent recovery preserves user flow).

    Only chunks stamped ``embedded = 1`` count on the SQLite side (the
    vector-write path flags them in the same transaction as ``add_vectors``),
    so selective embed policies — dependency doc pages only, ingestion
    pipelines without ``embed_chunks`` at all — are steady states, NOT
    drift: deliberately-unembedded chunks can never trigger the repair.
    """

    def _embedded_count() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM chunks WHERE embedded = 1").fetchone()[0]
        finally:
            conn.close()

    embedded_count = await asyncio.to_thread(_embedded_count)
    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=dim,
        bit_width=bit_width,
    ) as tq_uow:
        vec_count = tq_uow.size()
    if embedded_count == vec_count:
        return []

    logger.warning(
        "Cache integrity mismatch: embedded-flagged chunks=%d but TurboQuant "
        "index size=%d. Clearing content_hash on affected packages so the "
        "next indexing sweep re-extracts them.",
        embedded_count,
        vec_count,
    )

    def _clear_all_hashes() -> list[str]:
        conn = sqlite3.connect(str(db_path))
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM packages")]
            conn.execute("UPDATE packages SET content_hash = NULL")
            conn.commit()
            return names
        finally:
            conn.close()

    return await asyncio.to_thread(_clear_all_hashes)


def build_sqlite_chunk_hydrator(
    db_path: Path,
) -> Callable[[Sequence[int]], Awaitable[tuple[Chunk, ...]]]:
    """Build a ChunkHydrator — loads full ``Chunk`` objects for the given IDs.

    Used by ``TurboQuantVectorStore`` to turn vector hits (just IDs) back into
    rich ``Chunk`` records the retrieval pipeline can consume. Reuses
    ``row_to_chunk`` so the deserialisation contract matches the rest of
    the SQLite adapter — any schema drift surfaces uniformly.
    """
    provider = build_connection_provider(db_path)

    async def hydrate(ids: Sequence[int]) -> tuple[Chunk, ...]:
        if not ids:
            return ()
        id_list = list(ids)
        placeholders = ",".join("?" * len(id_list))
        # ``SELECT *`` keeps the column list in lockstep with the schema —
        # ``row_to_chunk`` reads named columns from the ``sqlite3.Row`` so
        # additive migrations (e.g., the v5 ``content_hash`` column) don't
        # require touching this query.
        sql = f"SELECT * FROM chunks WHERE id IN ({placeholders})"
        async with _maybe_acquire(provider) as conn:
            # Performance: ``row_to_chunk`` is pure CPU work — bundling
            # the fetch + map into a single ``to_thread`` call keeps the
            # whole hydration off the event-loop thread, matching the
            # ``SqliteChunkRepository.list`` pattern.
            return await asyncio.to_thread(
                lambda: tuple(row_to_chunk(r) for r in conn.execute(sql, id_list).fetchall())
            )

    return hydrate


@dataclass(frozen=True, slots=True)
class IndexerBundle:
    """Write-side wiring for one project's indexing runs.

    ``build_project_indexer`` is the single write-side composition root
    (the counterpart of ``server.py``'s read-side ``build_routers``): the
    CLI, the watch loop's reindex callback, tests, and any future
    programmatic consumer all get the same orchestrator + service +
    maintenance ops without re-deriving ~240 lines of wiring. The three
    callables close over the concrete SQLite / TurboQuant handles so the
    application-layer ``run_index_pass`` stays Protocol-only — Decision C
    (``IndexingService`` deliberately exposes no chunk-store handle) is
    preserved by shipping the FTS rebuild as a closure instead.
    """

    orchestrator: ProjectIndexer
    indexing_service: IndexingService
    uow_factory: Callable[[], CompositeUnitOfWork]
    pipeline_hash: str
    check_integrity: Callable[[], Awaitable[list[str]]]
    rebuild_fts: Callable[[], Awaitable[None]]
    stamp_metadata: Callable[[IndexMetadata], None]
    write_aggregates: Callable[[Path], Awaitable[None]]


def build_project_indexer(
    config: AppConfig,
    db_path: Path,
    *,
    use_inspect: bool,
    inspect_depth: int | None,
) -> IndexerBundle:
    """Assemble the write-side composition root for one project.

    Absorbs the wiring that previously lived inline in
    ``__main__._run_indexing``. ``inspect_depth=None`` falls back to the
    YAML ``extraction.members.inspect_depth``; the CLI resolves its
    ``--depth`` flag BEFORE calling (CLI flag wins over YAML — resolution
    stays client-side so this factory carries no argparse knowledge).
    The caller is responsible for having created the schema
    (``open_index_database(db_path).close()``) before repositories issue
    queries.

    Example::

        bundle = build_project_indexer(config, db_path, use_inspect=True, inspect_depth=None)
        stats = await run_index_pass(orchestrator=bundle.orchestrator, ...)
    """
    # Deferred imports are deliberate: they are the monkeypatch seams the
    # test suite relies on (tests/test_cli.py patches the module/package
    # attributes at call time), and they keep heavy optional deps
    # (fastembed/onnxruntime, the openai client) off the import path of
    # ``storage.factories`` consumers that never index.
    from pydocs_mcp.application import ProjectIndexer
    from pydocs_mcp.extraction import (
        AstMemberExtractor,
        InspectMemberExtractor,
        PipelineChunkExtractor,
        StaticDependencyResolver,
        build_ingestion_pipeline,
    )
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.retrieval.llm_clients import build_llm_client
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    # Hybrid-search composition root: source the write-side UoW children from
    # the SAME SearchBackend that retrieval + the benchmark use, so indexing
    # wires dense (and late-interaction, when enabled) consistently with the
    # read path — no separate child-assembly that could drift. The composite
    # UoW makes ``reindex_package`` write chunks AND vectors atomically, and
    # ``IndexingService`` + ``ProjectIndexer`` share the one factory so the
    # indexing transaction spans every backend without per-service branching.
    backend = build_search_backend(config, db_path)
    # Capability diagnostic (spec invariant C): one log line so an operator
    # can see at index time which retrieval capabilities the configured
    # backend actually serves — the visibility whose absence let the
    # dense/LI wiring bug stay silent.
    logger.info(format_capabilities(backend))
    uow_factory = build_composite_uow_factory(backend.write_uow_children())
    # ``.tq`` sidecar path for the integrity sweep. The backend derives its
    # TurboQuant sidecar as ``db_path.with_suffix(".tq")``; mirror that here
    # so the two always point at the same on-disk file. ``db_path`` already
    # carries the per-project ``<dirname>_<hash>`` slug (and any --cache-dir
    # override), so the suffix swap lands the sidecar beside the SQLite cache.
    tq_path = db_path.with_suffix(".tq")

    indexing_service = IndexingService(
        uow_factory=uow_factory,
        node_scores_enabled=config.reference_graph.node_scores.enabled,
    )

    # Construct the embedder once at startup so the rest of the pipeline
    # can share it. Failing here (e.g., OPENAI_API_KEY missing) surfaces
    # the issue immediately rather than at first query.
    embedder = build_embedder(config.embedding)
    # Compute the ingestion pipeline_hash ONCE at startup. This identity
    # slot (embedder + raw ingestion-YAML bytes) is threaded through the
    # BuildContext so ``AssignChunkContentHashStage`` can stamp every
    # chunk's content_hash with it. Any embedder swap or YAML edit
    # invalidates every chunk hash, the diff-merge sees them as 'added',
    # and the existing add path re-embeds them — no separate force-re-embed
    # code path needed.
    pipeline_hash = config.compute_ingestion_pipeline_hash()
    # Construct the LLM client once at startup so any future ingestion-time
    # LLM stage can be wired without another composition change. Symmetric
    # with ``embedder``: build once, thread through.
    llm_client = build_llm_client(config.llm)
    ingestion_pipeline = build_ingestion_pipeline(
        config,
        embedder=embedder,
        uow_factory=uow_factory,
        pipeline_hash=pipeline_hash,
        llm_client=llm_client,
    )
    chunk_extractor = PipelineChunkExtractor(pipeline=ingestion_pipeline)

    ast_member = AstMemberExtractor()
    members_cfg = config.extraction.members
    depth = inspect_depth if inspect_depth is not None else members_cfg.inspect_depth
    member_extractor = (
        InspectMemberExtractor(
            static_fallback=ast_member,
            depth=depth,
            members_per_module_cap=members_cfg.members_per_module_cap,
            signature_max_chars=members_cfg.signature_max_chars,
            docstring_max_chars=members_cfg.docstring_max_chars,
        )
        if use_inspect
        else ast_member
    )

    orchestrator = ProjectIndexer(
        indexing_service=indexing_service,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=chunk_extractor,
        member_extractor=member_extractor,
        uow_factory=uow_factory,
    )

    async def _check_integrity() -> list[str]:
        # Drift between SQLite and the ``.tq`` sidecar (process killed
        # mid-commit, etc.) is detected and repaired by clearing
        # ``packages.content_hash`` so the next pass re-extracts. No-op on a
        # fresh project (both counts == 0) and after an atomic clear_all.
        return await check_integrity_and_repair(
            db_path=db_path,
            tq_path=tq_path,
            dim=config.embedding.dim,
            bit_width=config.embedding.bit_width,
        )

    async def _rebuild_fts() -> None:
        # FTS rebuild is a maintenance op, not transactional — use a direct
        # SqliteChunkRepository handle here in the composition root
        # (Decision C: IndexingService no longer exposes a chunk_store).
        chunk_repo = SqliteChunkRepository(provider=build_connection_provider(db_path))
        await chunk_repo.rebuild_index()

    def _stamp_metadata(meta: IndexMetadata) -> None:
        stamp_conn = open_index_database(db_path)
        write_index_metadata(stamp_conn, meta)
        stamp_conn.close()

    write_aggregates = build_overview_aggregates_writer(
        config, db_path, uow_factory=uow_factory, llm_client=llm_client
    )

    return IndexerBundle(
        orchestrator=orchestrator,
        indexing_service=indexing_service,
        uow_factory=uow_factory,
        pipeline_hash=pipeline_hash,
        check_integrity=_check_integrity,
        rebuild_fts=_rebuild_fts,
        stamp_metadata=_stamp_metadata,
        write_aggregates=write_aggregates,
    )


def build_overview_aggregates_writer(
    config: AppConfig,
    db_path: Path,
    *,
    uow_factory: Callable[[], CompositeUnitOfWork],
    llm_client: LlmClient,
) -> Callable[[Path], Awaitable[None]]:
    """Build the index-end writer closure for the overview aggregates (blocks 9 + 2).

    Extracted from ``build_project_indexer`` so the write-side composition root
    stays under the complexity budget. Two independent halves, each gated by its
    own ``overview.*`` toggle and each writing only its column:

    - Block 9 (git activity): one extra bounded ``git log`` spawn per index
      (index-time only), reusing the 3a ``read_git_log`` + the pure
      ``compute_activity`` aggregator. Disabled → no spawn.
    - Block 2 (LLM architecture summary): the opt-in fingerprint-cached summary,
      reusing the ``llm_client`` already built for ingestion (no second client).
      Disabled → no client use. Fingerprint-cached, so the LLM call fires only
      when the module set changed; a malformed reply degrades to the old cache.

    ``None`` from either half leaves that column untouched (the mapper COALESCEs);
    nothing is written when both are absent. The per-half logic lives in
    module-level helpers so this stays a thin wiring closure.
    """

    async def _write_aggregates(project_root: Path) -> None:
        activity_json = await _compute_activity_json(config, project_root)
        overview_json = await _compute_summary_json(
            config, db_path, uow_factory=uow_factory, llm_client=llm_client
        )
        if activity_json is None and overview_json is None:
            return
        await asyncio.to_thread(_persist_overview_aggregates, db_path, activity_json, overview_json)

    return _write_aggregates


async def _compute_activity_json(config: AppConfig, project_root: Path) -> str | None:
    """§D17 block 9 half: framed ``git log`` → ``ActivitySummary`` JSON (or ``None``).

    Disabled / non-git tree / empty window → ``None`` (column left untouched).
    ``read_git_log`` is sync + subprocess-bound so the whole compute runs off the
    event loop in one ``to_thread`` hop.
    """
    activity_cfg = config.overview.git_activity
    if not activity_cfg.enabled:
        return None
    commit_cfg = config.decision_capture.commit_messages
    summary = await asyncio.to_thread(
        _read_and_aggregate_activity,
        project_root,
        commit_cfg.max_commits,
        commit_cfg.timeout_seconds,
        activity_cfg.window_days,
    )
    return activity_to_json(summary) if summary is not None else None


def _read_and_aggregate_activity(
    project_root: Path,
    max_commits: int,
    timeout_seconds: int,
    window_days: int,
) -> ActivitySummary | None:
    # Reuses the 3a ``read_git_log`` (bounded by the SAME ``commit_messages``
    # window the decision miner uses — one git-log budget, not two) and the pure
    # ``compute_activity`` aggregator.
    from pydocs_mcp.extraction.decisions._git import read_git_log

    log_text = read_git_log(project_root, max_commits=max_commits, timeout_seconds=timeout_seconds)
    return compute_activity(log_text, window_days=window_days, now=time.time())


async def _compute_summary_json(
    config: AppConfig,
    db_path: Path,
    *,
    uow_factory: Callable[[], CompositeUnitOfWork],
    llm_client: LlmClient,
) -> str | None:
    """§D17 block 2 half: opt-in fingerprint-cached LLM summary → JSON (or ``None``).

    Disabled → no client use, no write. Fingerprint-cached: a cache hit returns
    the SAME record (nothing new to persist → ``None``); only a genuinely-fresh
    summary yields JSON. A malformed reply degrades to the old cache inside
    ``generate_overview_summary``.
    """
    if not config.overview.llm_summary.enabled:
        return None
    module_qnames, central, cached = await _read_summary_inputs(db_path, uow_factory)
    if not module_qnames:
        return None
    produced = await generate_overview_summary(
        module_qnames=module_qnames,
        central_symbols=central,
        llm_client=llm_client,
        cached=cached,
        now=time.time(),
    )
    if produced is None or produced == cached:
        return None
    return summary_to_json(produced)


async def _read_summary_inputs(
    db_path: Path,
    uow_factory: Callable[[], CompositeUnitOfWork],
) -> _SummaryInputs:
    """Read the module map + most-central symbols + the cached summary.

    The LLM summary is grounded in the just-indexed project's module map and its
    most-central symbols (pagerank desc). Reading the cached summary here lets
    ``generate_overview_summary`` skip the LLM call when the fingerprint matches.
    """
    async with uow_factory() as uow:
        trees = await uow.trees.load_all_in_package(PROJECT_PACKAGE_NAME)
        scores = await uow.node_scores.for_package(PROJECT_PACKAGE_NAME)
    module_qnames = tuple(trees)
    central = tuple(s.qualified_name for s in sorted(scores, key=lambda s: -s.pagerank))
    cached = summary_from_json(_read_stored_overview_json(db_path) or "")
    return module_qnames, central, cached


def _read_stored_overview_json(db_path: Path) -> str | None:
    conn = open_index_database(db_path)
    try:
        _activity_json, overview_json = read_overview_aggregates(conn)
    finally:
        conn.close()
    return overview_json


def _persist_overview_aggregates(
    db_path: Path,
    activity_json: str | None,
    overview_json: str | None,
) -> None:
    conn = open_index_database(db_path)
    try:
        update_overview_aggregates(conn, activity_json=activity_json, overview_json=overview_json)
    finally:
        conn.close()


def build_freshness_probe(
    *,
    db_path: Path,
    project_root: Path,
    enabled: bool,
    ttl_seconds: float,
) -> IndexFreshnessProbe:
    """Freshness probe for one loaded db — sync closures, threaded by the probe."""

    def _read() -> IndexMetadata | None:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            return read_index_metadata(conn)
        finally:
            conn.close()

    def _count() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        finally:
            conn.close()

    return IndexFreshnessProbe(
        enabled=enabled,
        ttl_seconds=ttl_seconds,
        read_metadata=_read,
        resolve_live_head=lambda: resolve_git_head(project_root),
        count_packages=_count,
    )


# WHY the non-.db suffix: discover_workspace globs *.db; the overlay must
# never be mis-loaded as a bundle (spec 2026-07-11 §3.1).
_OVERLAY_FILENAME = "pydocs-links.sqlite3"


def overlay_path_for(workspace: Path | None, db_paths: tuple[Path, ...]) -> Path:
    """Resolve the cross-link overlay sidecar location (spec §3.1).

    Workspace mode → workspace-local file. Explicit ``--db a.db --db b.db``
    mode (no workspace dir) → a home-cache file keyed by the sorted tuple of
    resolved bundle paths, so the same bundle set always maps to one overlay.

    Example:
        >>> overlay_path_for(Path("/bundles"), ()).name
        'pydocs-links.sqlite3'
    """
    if workspace is not None:
        return workspace / _OVERLAY_FILENAME
    import hashlib

    key = "\n".join(sorted(str(p.resolve()) for p in db_paths))
    # md5 as a fast non-cryptographic fingerprint (the db.py cache-slug
    # precedent); usedforsecurity=False signals intent to ruff/bandit.
    digest = hashlib.md5(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    return Path("~/.pydocs-mcp/links").expanduser() / f"{digest}.sqlite3"


def build_cross_link_store(path: Path) -> SqliteCrossLinkStore:
    """Build the persisted overlay store bound to ``path`` (spec §3.2)."""
    from pydocs_mcp.storage.sqlite.cross_link_store import SqliteCrossLinkStore

    return SqliteCrossLinkStore(path=path)
