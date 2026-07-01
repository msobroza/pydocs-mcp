"""CLI: ``python -m pydocs_mcp {serve,index,query,api} /path/to/project``.

Each subcommand is a thin wrapper over the application-layer services
(spec §5.6, AC #9, #16):

* ``serve`` / ``index`` route through :class:`ProjectIndexer`.
* ``query`` / ``api`` route through :class:`DocsSearch` /
  :class:`ApiSearch`, rendering the top composite chunk's text.

Every subcommand delegates to :func:`_run_cmd`, the single top-level
exception handler. On failure it prints ``Error: <msg>`` to stderr and
exits non-zero. Under ``-v``/``--verbose`` it additionally prints the
traceback (via ``traceback.print_exc`` plus ``log.exception`` for
structured-log consumers); without it, a one-line hint points users at
``--verbose`` and only ``log.error`` records the failure so the
traceback stays out of the user's stderr pipeline. The four
``# noqa: BLE001`` annotations previously attached to each ``_cmd_*``
collapse into one inside ``_run_cmd``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import WatchConfig
    from pydocs_mcp.serve.watcher import FileWatcher

from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
from pydocs_mcp.db import (
    cache_path_for_project,
    open_index_database,
)

log = logging.getLogger("pydocs-mcp")


# ── Argument parsing ──────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse tree — kept as a named helper so tests can
    build the parser without triggering ``main``'s dispatch logic."""
    p = argparse.ArgumentParser(
        prog="pydocs-mcp",
        description="Local Python docs MCP server (optionally Rust-accelerated)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", type=Path, help="Path to pydocs-mcp.yaml")
    sub = p.add_subparsers(dest="cmd")

    _no_rust = dict(
        action="store_true",
        help="Force pure-Python fallback even if Rust extension is available.",
    )
    # ``--cache-dir`` overrides the directory the SQLite cache (and ``.tq``
    # sidecar) live in. CLI-only knob — never plumbed through to the MCP
    # tool surface. Common to every subcommand so the four wirings stay in
    # sync. (Per-deployment knob; no impact on the fixed 2-tool MCP API.)
    _cache_dir = dict(
        type=Path,
        default=None,
        help="Override the cache directory (default: ~/.pydocs-mcp).",
    )
    # Re-declaring ``-v/--verbose`` on each subparser so it parses
    # regardless of position (``-m pydocs_mcp -v search …`` and
    # ``-m pydocs_mcp search … -v`` both work). ``default=argparse.SUPPRESS``
    # is the trick: when the subparser's ``-v`` is absent the namespace
    # keeps whatever value the top-level parser already assigned, so a
    # leading ``-v`` is never silently clobbered.
    _verbose = dict(
        action="store_true",
        default=argparse.SUPPRESS,
        help="Verbose logging + traceback on failure.",
    )

    # ``watch`` is the standalone watcher counterpart to ``serve --watch``:
    # the whole subcommand IS watch mode (it does NOT accept ``--watch``,
    # which would be redundant noise). Shares every other knob with the
    # ``serve`` / ``index`` family so operators don't relearn flags when
    # picking between the two modes.
    for cmd, hlp in [
        ("serve", "Index + start MCP"),
        ("index", "Index only"),
        ("watch", "Index + watch project for changes (no MCP server)"),
    ]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("project", nargs="?", default=".")
        # default=None so the YAML-configured inspect_depth wins when the
        # flag is absent (without this, argparse's hard-coded default
        # silently shadows ``extraction.members.inspect_depth``, mirroring
        # the F11 dead-config defect /ultrareview just removed for
        # by_extension).
        sp.add_argument(
            "--depth",
            type=int,
            default=None,
            help="Submodule scan depth (default: YAML extraction.members.inspect_depth)",
        )
        sp.add_argument("--workers", type=int, default=4, help="Parallel workers")
        sp.add_argument("--force", action="store_true", help="Clear cache, re-index all")
        sp.add_argument("--skip-project", action="store_true", help="Skip project source")
        sp.add_argument(
            "--skip-deps",
            action="store_true",
            help="Skip dependency indexing — index only the project source.",
        )
        sp.add_argument("--no-rust", **_no_rust)
        sp.add_argument("--cache-dir", **_cache_dir)
        sp.add_argument("-v", "--verbose", **_verbose)
        sp.add_argument(
            "--no-inspect",
            action="store_true",
            help="Don't import deps. Read .py files from site-packages instead. "
            "Faster, safer, no side-effects. Uses the same parser as project source.",
        )
        sp.add_argument(
            "--gpu",
            action="store_true",
            help="Run embedder inference on CUDA. Requires the matching GPU "
            "runtime (onnxruntime-gpu / fastembed-gpu / CUDA torch). Does not "
            "trigger a re-index (device is excluded from the cache key).",
        )
        if cmd == "serve":
            sp.add_argument(
                "--watch",
                action="store_true",
                help="Watch the project for changes and reindex on edits. "
                "Requires the 'watch' extras: pip install pydocs-mcp[watch]",
            )

    # sub-PR #6: replace query/api with 2 tools matching the MCP surface.
    sp_search = sub.add_parser(
        "search",
        help="Hybrid keyword + semantic search over project + deps",
        description=(
            "Hybrid keyword + semantic search across your project's source AND every "
            "installed dependency (docs + code), ranked by BM25 plus dense embeddings. "
            "Use --package __project__ or --scope project to restrict to YOUR code, "
            "not a library."
        ),
        epilog=(
            "Examples:\n"
            "  pydocs-mcp search 'batch inference' --kind docs\n"
            "  pydocs-mcp search HTTPBasicAuth --kind api\n"
            "  pydocs-mcp search 'retry logic' --package requests\n"
            "  pydocs-mcp search parser --scope project\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_search.add_argument(
        "query", help="Search terms (space-separated; prose AND identifiers work)"
    )
    sp_search.add_argument(
        "--kind",
        choices=["docs", "api", "any"],
        default="any",
        help="Which index to search: 'docs' = prose / README, 'api' = functions / classes, 'any' = both (default).",
    )
    sp_search.add_argument(
        "-p",
        "--package",
        dest="package",
        default="",
        help='Restrict to one package (e.g. "fastapi"). Use "__project__" for YOUR code, not a library. Default: all packages.',
    )
    sp_search.add_argument(
        "--scope",
        choices=["project", "deps", "all"],
        default="all",
        help='Restrict by scope: "project" = your code only, "deps" = installed deps only, "all" = both (default). Use "project" when the user asks about THEIR code.',
    )
    sp_search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max number of results (1-1000, default: 10).",
    )
    sp_search.add_argument(
        "--project-dir",
        dest="project",
        default=".",
        help="Path to the project root (default: current directory). Determines which cache database is loaded.",
    )
    sp_search.add_argument("--no-rust", **_no_rust)
    sp_search.add_argument("--cache-dir", **_cache_dir)
    sp_search.add_argument("-v", "--verbose", **_verbose)

    sp_lookup = sub.add_parser(
        "lookup",
        help="Navigate to a known symbol + walk its reference graph",
        description=(
            "Navigate to a known symbol (dotted path) and optionally traverse its "
            "reference graph — callers, callees, base classes. Use this when you "
            "know the exact target; use 'search' when you only have a keyword or topic."
        ),
        epilog=(
            "Examples:\n"
            "  pydocs-mcp lookup                                                           # list all indexed packages\n"
            "  pydocs-mcp lookup fastapi                                                   # package overview\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter                                 # class + members\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers   # who calls this method\n"
            "  pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits               # base classes\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show impact    # what breaks if I change it\n"
            "  pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show context   # everything to understand it\n"
            "  pydocs-mcp lookup __project__.my_module.MyClass                             # YOUR class, not a library\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_lookup.add_argument(
        "target",
        nargs="?",
        default="",
        help='Dotted path (e.g. "fastapi.routing.APIRouter"). Use "__project__.<module>.<symbol>" for YOUR code. Empty = list all indexed packages.',
    )
    sp_lookup.add_argument(
        "--show",
        choices=["default", "tree", "callers", "callees", "inherits", "impact", "context"],
        default="default",
        help=(
            "What to show: 'default' = symbol summary + immediate children (start here); "
            "'tree' = full nested subtree (use when 'default' is too shallow); "
            "'callers' = who references this — use to answer 'who uses X?'; "
            "'callees' = what this calls — use to answer 'what does X depend on?'; "
            "'inherits' = base classes / interface chain — use to answer 'what does X extend?'; "
            "'impact' = everything that transitively calls this, ranked — 'what breaks if I change X?'; "
            "'context' = dependency closure packed under a token budget — 'everything to understand X'."
        ),
    )
    sp_lookup.add_argument(
        "--project-dir",
        dest="project",
        default=".",
        help="Path to the project root (default: current directory). Determines which cache database is loaded.",
    )
    sp_lookup.add_argument("--no-rust", **_no_rust)
    sp_lookup.add_argument("--cache-dir", **_cache_dir)
    sp_lookup.add_argument("-v", "--verbose", **_verbose)

    return p


# ── Shared setup helpers ──────────────────────────────────────────────────


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _apply_no_rust_flag(args: argparse.Namespace) -> None:
    """Flip the Rust/Python toggle once, logging the decision."""
    if getattr(args, "no_rust", False) and RUST_AVAILABLE:
        disable_rust()
        log.info("Engine: Python (Rust disabled via --no-rust)")
    else:
        log.info("Engine: %s", "Rust" if RUST_AVAILABLE else "Python")


def _project_and_db(args: argparse.Namespace) -> tuple[Path, Path]:
    project = Path(getattr(args, "project", ".")).resolve()
    db_path = cache_path_for_project(project)
    cache_dir = getattr(args, "cache_dir", None)
    if cache_dir is not None:
        # Preserve the per-project ``<dirname>_<hash>.db`` slug computed by
        # ``cache_path_for_project`` so multiple projects keep separate
        # state under the overridden root. The ``.tq`` (and ``.plaid``)
        # sidecars the indexing path derives via ``db_path.with_suffix(...)``
        # share this slug, so the SQLite cache and its sidecars always land
        # side-by-side under whatever cache root the CLI picked.
        db_path = Path(cache_dir) / db_path.name
    log.debug("DB: %s", db_path)
    return project, db_path


# ── Subcommand handlers ───────────────────────────────────────────────────


async def _run_indexing(args: argparse.Namespace) -> None:
    """Run :class:`ProjectIndexer` end-to-end for ``index`` / ``serve``.

    Kept as a module-level coroutine so both ``_cmd_index`` and
    ``_cmd_serve`` can drive it through a single ``asyncio.run`` — mirrors
    the pre-PR pattern where one event loop wrapped the whole indexing
    phase so sub-loops (async SQLite writes, ``to_thread`` extractions)
    shared the same context.

    Wires the strategy-based classes from :mod:`pydocs_mcp.extraction`:
    :class:`PipelineChunkExtractor` (driven by the YAML ingestion pipeline),
    :class:`InspectMemberExtractor` (with :class:`AstMemberExtractor` fallback)
    or plain :class:`AstMemberExtractor` for ``--no-inspect``, and
    :class:`StaticDependencyResolver`.
    """
    from pydocs_mcp.application import ProjectIndexer
    from pydocs_mcp.db import build_connection_provider
    from pydocs_mcp.extraction import (
        AstMemberExtractor,
        InspectMemberExtractor,
        PipelineChunkExtractor,
        StaticDependencyResolver,
        build_ingestion_pipeline,
    )
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.llm_clients import build_llm_client
    from pydocs_mcp.storage.factories import (
        build_composite_uow_factory,
        check_integrity_and_repair,
    )
    from pydocs_mcp.storage.search_backend import (
        build_search_backend,
        format_capabilities,
    )
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository

    project, db_path = _project_and_db(args)

    # Ensure the schema exists before repositories issue queries.
    open_index_database(db_path).close()

    use_inspect = not args.no_inspect
    mode = "inspect" if use_inspect else "static"
    log.info("Project: %s (mode=%s)", project, mode)

    # Sub-PR #5: build THE ingestion pipeline once and share it across
    # project + dependency extraction. ``AppConfig.load`` honours the
    # optional ``--config`` override the CLI already passes to search /
    # serve so ingestion pipeline overrides (spec §7.3) stay consistent
    # with the rest of the config.
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    config = config.with_device(gpu=getattr(args, "gpu", False))
    # Push YAML-loaded settings into module-level slots read by
    # ``LookupInput`` validators and ``ReferenceCaptureStage`` (sub-PR #5c
    # Task 8). Indexing uses the latter via ``ReferenceCaptureStage`` in
    # the ingestion pipeline; reads use the former.
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config

    configure_from_app_config(config)

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
    log.info(format_capabilities(backend))
    uow_factory = build_composite_uow_factory(backend.write_uow_children())
    # ``.tq`` sidecar path for the integrity sweep below. The backend derives
    # its TurboQuant sidecar as ``db_path.with_suffix(".tq")``; mirror that
    # here so the two always point at the same on-disk file. ``db_path``
    # already carries the per-project ``<dirname>_<hash>`` slug (and any
    # ``--cache-dir`` override), so the suffix swap lands the sidecar right
    # beside the SQLite cache under whatever root the CLI picked.
    tq_path = db_path.with_suffix(".tq")
    # Cache integrity sweep — drift between SQLite and the ``.tq`` sidecar
    # (process killed mid-commit, etc.) is detected and repaired by
    # clearing ``packages.content_hash`` so the next pass re-extracts.
    # No-op on a fresh project (both counts == 0). Under ``--force`` the
    # subsequent ``index_project(force=True)`` calls ``IndexingService.clear_all``
    # which atomically wipes SQLite + TurboQuant via the composite UoW —
    # post-clear, chunks=vectors=0, so this check sees a consistent state
    # and is a clean no-op rather than the false-positive trigger it would
    # have been before clear_all was atomic.
    repaired = await check_integrity_and_repair(
        db_path=db_path,
        tq_path=tq_path,
        dim=config.embedding.dim,
        bit_width=config.embedding.bit_width,
    )
    if repaired:
        log.warning(
            "Cache integrity: cleared content_hash on %d package(s); "
            "they will be re-extracted this run",
            len(repaired),
        )

    from pydocs_mcp.application.indexing_service import IndexingService

    indexing_service = IndexingService(
        uow_factory=uow_factory,
        node_scores_enabled=config.reference_graph.node_scores.enabled,
    )

    # Detect a model rename in YAML — packages tagged with the old
    # ``embedding_model`` carry vectors that the new model cannot match
    # at query time (different vector space). Clearing ``content_hash``
    # routes them through the existing hash-skip path so the next sweep
    # re-extracts + re-embeds them under the current model. Skipped
    # under ``--force``: that path already wipes the cache wholesale.
    if not args.force:
        from dataclasses import replace as dc_replace

        stale_pkg_names = await indexing_service.find_stale_packages(
            current_model=config.embedding.model_name,
        )
        if stale_pkg_names:
            log.warning(
                "Embedding model changed; re-embedding %d package(s): %s",
                len(stale_pkg_names),
                ", ".join(stale_pkg_names),
            )
            async with uow_factory() as uow:
                for name in stale_pkg_names:
                    pkg = await uow.packages.get(name)
                    if pkg is not None:
                        # Empty content_hash will not equal the freshly-
                        # extracted package's real hash, so the skip check
                        # in ProjectIndexer (existing.content_hash ==
                        # pkg.content_hash) falls through to a full reindex.
                        await uow.packages.upsert(
                            dc_replace(pkg, content_hash=""),
                        )
                await uow.commit()

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
    # code path needed (spec Decisions 4 + 12).
    pipeline_hash = config.compute_ingestion_pipeline_hash()
    # Construct the LLM client once at startup so any future ingestion-time
    # LLM stage can be wired without another composition change. Symmetric
    # with ``embedder``: build once, thread through. No shipped ingestion
    # stage consumes ``llm_client`` today; the retrieval pipeline does
    # (LlmTreeReasoningStep) but goes through ``build_retrieval_context``
    # which constructs its own client.
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
    # CLI flag wins over YAML; YAML wins over hard-coded fallback. This
    # mirrors the pattern for every other tunable knob — undocumented
    # defaults at the wiring layer become silent traps.
    inspect_depth = args.depth if args.depth is not None else members_cfg.inspect_depth
    member_extractor = (
        InspectMemberExtractor(
            static_fallback=ast_member,
            depth=inspect_depth,
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

    if args.force:
        log.info("Cache cleared")

    stats = await orchestrator.index_project(
        project,
        force=args.force,
        include_project_source=not args.skip_project,
        include_dependencies=not args.skip_deps,
        workers=args.workers,
    )
    # FTS rebuild is a maintenance op, not transactional — use a direct
    # SqliteChunkRepository handle. Post-#5a-2 IndexingService no longer
    # exposes a chunk_store attribute (Decision C).
    chunk_repo = SqliteChunkRepository(provider=build_connection_provider(db_path))
    await chunk_repo.rebuild_index()

    kb = db_path.stat().st_size / 1024 if db_path.exists() else 0.0
    log.info(
        "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
        stats.indexed,
        stats.cached,
        stats.failed,
        kb,
    )


async def _run_serve_indexing(args: argparse.Namespace) -> None:
    """Async indexing phase of ``serve`` — runs before the MCP server boots.

    Split out from the blocking ``server.run`` call so the indexing build-up
    can route through ``_run_cmd``'s ``--verbose`` / traceback policy while
    the MCP server itself runs on the main thread (see ``_cmd_serve`` for
    the SIGINT rationale).
    """
    await _run_indexing(args)


def _build_watcher_and_callback(
    args: argparse.Namespace,
    watch_cfg: WatchConfig,
) -> tuple[FileWatcher, Callable[[], Awaitable[None]]]:
    """Build the ``FileWatcher`` + ``on_change`` callback shared by
    ``serve --watch`` and the standalone ``watch`` subcommand.

    Single source of truth for watcher construction so the two modes can
    only differ in whether they ALSO run an MCP server. Lifted out of
    ``_run_watch_loop`` to keep the two consumers in sync — bug-fixes
    or YAML-knob additions land here and reach both modes automatically.
    """
    from pydocs_mcp.serve.watcher import FileWatcher

    project, _db = _project_and_db(args)
    watcher = FileWatcher(
        root=project,
        extensions=tuple(watch_cfg.extensions),
        ignore_globs=tuple(watch_cfg.ignore_globs),
        debounce_ms=watch_cfg.debounce_ms,
    )

    async def _on_change() -> None:
        # Reindex via the same Phase 1 helper used at startup. Cache
        # makes the no-change case <100ms (spec §2).
        try:
            await _run_indexing(args)
        except Exception as exc:
            # WHY: a reindex failure during the watch loop should NOT
            # take down the consumer (MCP server in --watch mode; the
            # whole process in standalone watch mode). Log + keep
            # serving stale data instead.
            log.error("watch: reindex failed: %s", exc)

    return watcher, _on_change


async def _run_watch_loop(
    args: argparse.Namespace,
    *,
    db_path: Path | None = None,
) -> None:
    """Run the MCP server (Phase 2) AND the file watcher concurrently.

    Spec §4.1 deliverable 5: ``--watch`` adds a third element to
    ``_cmd_serve`` — the watcher asyncio task. The MCP server still runs
    on the main thread (CQ-1 SIGINT delivery preserved); the watcher
    runs on the asyncio loop in a worker thread via ``asyncio.to_thread``.

    Try/finally guarantees the watcher task is cancelled regardless of
    how ``run(...)`` exits (KeyboardInterrupt, RuntimeError, etc.) —
    pins Risk R4 (no orphan Observer on crash) + spec Decision G.
    """
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import run

    project, resolved_db = _project_and_db(args)
    if db_path is None:
        db_path = resolved_db

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    watch_cfg = config.serve.watch

    watcher, on_change = _build_watcher_and_callback(args, watch_cfg)

    watcher_task = asyncio.create_task(watcher.run_until_cancelled(on_change))
    log.info("watch: started (debounce=%dms, root=%s)", watch_cfg.debounce_ms, project)
    try:
        # ``run(...)`` is blocking; offload to a worker thread so the
        # watcher_task keeps draining events on the asyncio loop.
        await asyncio.to_thread(run, db_path, config_path=getattr(args, "config", None))
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("watch: watcher task exited with %s", exc)


async def _run_watch_only(args: argparse.Namespace) -> None:
    """Run only the file watcher — no MCP server.

    Used by the standalone ``pydocs-mcp watch`` subcommand for operators
    who want a fresh on-disk index for CLI ``search`` / ``lookup`` calls
    without keeping an idle FastMCP stdio server running. Blocks on
    ``watcher.run_until_cancelled`` until the task is cancelled
    (KeyboardInterrupt-driven cancellation propagates through
    ``asyncio.run`` in ``_cmd_watch``).
    """
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    watch_cfg = config.serve.watch

    watcher, on_change = _build_watcher_and_callback(args, watch_cfg)
    project, _db = _project_and_db(args)
    log.info(
        "watch (CLI-only): started (debounce=%dms, root=%s, MCP server: off)",
        watch_cfg.debounce_ms,
        project,
    )
    await watcher.run_until_cancelled(on_change)


async def _run_search(args: argparse.Namespace) -> None:
    """Mirror the MCP ``search`` tool: Pydantic input + same pipelines +
    same rendering. kind='any' runs chunks and members in parallel (§8)."""
    from pydocs_mcp.application import (
        ApiSearch,
        DocsSearch,
        SearchInput,
    )
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.server import _do_search

    _project, db_path = _project_and_db(args)
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    # Push YAML-loaded settings into module-level slots (sub-PR #5c
    # Task 8). ``search`` itself doesn't consume the reference-graph
    # config, but the call is uniform across every CLI command so a
    # follow-up search subcommand can rely on it.
    configure_from_app_config(config)
    context = build_retrieval_context(db_path, config)
    docs_svc = DocsSearch(
        chunk_pipeline=build_chunk_pipeline_from_config(config, context),
    )
    api_svc = ApiSearch(
        member_pipeline=build_member_pipeline_from_config(config, context),
    )

    payload = SearchInput(
        query=args.query,
        kind=args.kind,
        package=args.package,
        scope=args.scope,
        limit=args.limit,
    )
    print(await _do_search(payload, docs_svc, api_svc))


async def _run_lookup(args: argparse.Namespace) -> None:
    """Mirror the MCP ``lookup`` tool — same LookupService dispatch.

    Delegates wiring to :func:`build_sqlite_lookup_service` so the CLI and
    the MCP server can never drift on which stores back ``lookup``.
    """
    from pydocs_mcp.application import LookupInput
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    _project, db_path = _project_and_db(args)
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    # Push YAML-loaded settings into module-level slots (sub-PR #5c
    # Task 8). ``LookupInput`` validators read ``_LIMIT_DEFAULT`` /
    # ``_LIMIT_MAX`` from this for show='callers'|'callees' bounds.
    configure_from_app_config(config)
    svc = build_sqlite_lookup_service(db_path, config=config)

    payload = LookupInput(target=args.target, show=args.show)
    print(await svc.lookup(payload))


def _run_cmd(coro: Awaitable[None], *, verbose: bool) -> int:
    """Single top-level exception boundary for every CLI subcommand.

    Wraps the per-command coroutine in one ``try / except Exception`` so
    diagnostic policy lives in one place instead of being copy-pasted into
    four ``_cmd_*`` bodies. Under ``--verbose`` the full traceback lands
    on stderr (via ``traceback.print_exc``) and the logger records the
    failure with traceback via ``log.exception`` for any structured-log
    consumer that swapped out the default stderr handler. Without
    ``--verbose`` only the short ``Error: <msg>`` line plus a hint to
    re-run verbose are printed — the traceback stays out of the user's
    output pipeline, and only ``log.error`` (no traceback) is recorded
    so the default stderr-attached logger never leaks the traceback.
    """
    try:
        asyncio.run(coro)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc(file=sys.stderr)
            # ``log.exception`` also includes the traceback; with the
            # default ``_configure_logging(verbose=True)`` handler aimed
            # at ``sys.stderr`` this duplicates ``print_exc`` above. The
            # duplication is intentional for the structured-log consumer
            # case — if a user reconfigures the logger to a file or a
            # JSON formatter, they still need the traceback. ``print_exc``
            # alone wouldn't reach a non-stderr handler.
            log.exception("CLI command failed")
        else:
            print("(re-run with --verbose to see the traceback)", file=sys.stderr)
            log.error("CLI command failed: %s", exc)
        return 1


def _cmd_index(args: argparse.Namespace) -> int:
    return _run_cmd(_run_indexing(args), verbose=args.verbose)


def _cmd_serve(args: argparse.Namespace) -> int:
    # Phase 1 — async indexing through ``_run_cmd`` so the verbose /
    # traceback policy applies to indexing failures uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    _project, db_path = _project_and_db(args)

    if getattr(args, "watch", False):
        # Phase 2 (--watch path): server + watcher concurrently via
        # ``_run_watch_loop``. ``run(...)`` is offloaded to a worker
        # thread inside ``_run_watch_loop`` so the watcher's asyncio
        # consumer keeps draining events.
        #
        # WHY this differs from the no-watch path: without `--watch`,
        # `run(...)` is the only thing happening on the main thread, so
        # SIGINT reaches it directly. With `--watch`, the asyncio loop
        # is also running here, so the loop owns SIGINT; `run(...)`
        # exits via thread-pool unwind when the loop is cancelled.
        try:
            asyncio.run(_run_watch_loop(args, db_path=db_path))
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc(file=sys.stderr)
                log.exception("CLI command failed")
            else:
                print("(re-run with --verbose to see the traceback)", file=sys.stderr)
                log.error("CLI command failed: %s", exc)
            return 1

    # Phase 2 (no-watch path) — unchanged from today.
    # ``server.run`` calls ``anyio.run(self.run_stdio_async)`` internally,
    # which starts its own event loop. Running that inside
    # ``asyncio.to_thread`` would dispatch it to a worker thread, but
    # Python only delivers SIGINT to the main thread and
    # ``asyncio.to_thread`` cannot cancel a running thread — so Ctrl+C
    # against ``pydocs-mcp serve`` would not interrupt cleanly. Run on
    # the main thread so the default SIGINT handler reaches the blocking
    # loop. The try / except mirrors ``_run_cmd``'s policy.
    from pydocs_mcp.server import run

    try:
        run(
            db_path,
            config_path=getattr(args, "config", None),
            gpu=getattr(args, "gpu", False),
        )
        return 0
    except KeyboardInterrupt:
        # Graceful shutdown via Ctrl+C — not an error.
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
            log.exception("CLI command failed")
        else:
            print("(re-run with --verbose to see the traceback)", file=sys.stderr)
            log.error("CLI command failed: %s", exc)
        return 1


def _cmd_watch(args: argparse.Namespace) -> int:
    """Standalone watcher mode: index once + watch + reindex on edits.

    No MCP server runs in this path — for users who want fresh index
    state without an idle FastMCP stdio process. Same two-phase shape
    as ``_cmd_serve`` (initial index, then loop) but Phase 2 here is
    the watcher loop only.
    """
    # Phase 1: initial indexing (same as ``serve`` / ``index`` does at
    # startup). Routes through ``_run_cmd`` so the --verbose / traceback
    # policy applies uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    # Phase 2 (watcher-only) — own asyncio.run so SIGINT (KeyboardInterrupt)
    # propagates through the asyncio loop and cancels the watcher's
    # ``run_until_cancelled``, which then tears down the Observer via
    # the try/finally inside ``FileWatcher.run_until_cancelled``.
    try:
        asyncio.run(_run_watch_only(args))
        return 0
    except KeyboardInterrupt:
        # Graceful shutdown via Ctrl+C — not an error.
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
            log.exception("CLI command failed")
        else:
            print("(re-run with --verbose to see the traceback)", file=sys.stderr)
            log.error("CLI command failed: %s", exc)
        return 1


def _cmd_search(args: argparse.Namespace) -> int:
    return _run_cmd(_run_search(args), verbose=args.verbose)


def _cmd_lookup(args: argparse.Namespace) -> int:
    return _run_cmd(_run_lookup(args), verbose=args.verbose)


# ── Entry point ───────────────────────────────────────────────────────────


_CMD_TABLE = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "watch": _cmd_watch,
    "search": _cmd_search,
    "lookup": _cmd_lookup,
}


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)

    if not args.cmd:
        parser.print_help()
        return 0

    _apply_no_rust_flag(args)
    handler = _CMD_TABLE[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
