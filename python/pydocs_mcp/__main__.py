"""CLI: ``python -m pydocs_mcp
{serve,index,watch,search,overview,symbol,context,refs,why,lookup}``.

Each subcommand is a thin wrapper over the application-layer services:

* ``serve`` / ``index`` / ``watch`` route through :class:`ProjectIndexer`.
* ``search`` / ``overview`` / ``symbol`` / ``context`` / ``refs`` / ``why``
  mirror the six task-shaped MCP tools 1:1 (``search_codebase``,
  ``get_overview``, ``get_symbol``, ``get_context``, ``get_references``,
  ``get_why``); ``lookup`` is the deprecated alias that routes onto
  symbol/refs/context (and overview for an empty target).

Every subcommand routes its failures through :func:`_report_cli_failure`,
the single owner of the diagnostic policy: on failure it prints
``Error: <msg>`` to stderr and exits non-zero. Under ``-v``/``--verbose``
it additionally prints the traceback (via ``traceback.print_exc`` plus
``log.exception`` for structured-log consumers); without it, a one-line
hint points users at ``--verbose`` and only ``log.error`` records the
failure so the traceback stays out of the user's stderr pipeline. Async
commands enter via :func:`_run_cmd`; the blocking serve/watch entry
points enter via :func:`_run_blocking`, which adds the
KeyboardInterrupt-as-success contract (Ctrl+C is a graceful shutdown).
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
    from pydocs_mcp.retrieval.config import AppConfig, WatchConfig
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
    p.add_argument(
        "--config",
        type=Path,
        help="Path to pydocs-mcp.yaml (must precede the subcommand: "
        "pydocs-mcp --config x.yaml serve .)",
    )
    sub = p.add_subparsers(dest="cmd")

    _no_rust = dict(
        action="store_true",
        help="Force pure-Python fallback even if Rust extension is available.",
    )
    # ``--cache-dir`` overrides the directory the SQLite cache (and ``.tq``
    # sidecar) live in. CLI-only knob — never plumbed through to the MCP
    # tool surface. Common to every subcommand so the four wirings stay in
    # sync. (Per-deployment knob; no impact on the fixed six-tool MCP API.)
    _cache_dir = dict(
        type=Path,
        default=None,
        help="Override the cache directory (default: ~/.pydocs-mcp).",
    )
    # Multi-repo loading (CLI-only knob). ``--workspace`` loads every pre-built
    # ``.db`` bundle in a directory; ``--db`` loads specific bundles (repeatable).
    # Both are READ-ONLY (the real source may be absent, so no reindex/watch). The
    # per-query ``--project`` scope selects among what was loaded.
    _workspace = dict(
        type=Path,
        default=None,
        metavar="DIR",
        help="Load every pre-built .db bundle in DIR (read-only multi-repo).",
    )
    _db = dict(
        type=Path,
        action="append",
        default=None,
        dest="db_paths",
        metavar="FILE",
        help="Load a specific pre-built .db bundle (repeatable; read-only).",
    )
    _project_scope = dict(
        default="",
        dest="project_scope",
        metavar="NAME",
        help="Restrict the query to one loaded project by name (default: all loaded).",
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

    # The shared corpus-selector + engine knobs every query subcommand carries.
    # Single source of truth so the six task-shaped subcommands (plus the
    # deprecated ``lookup`` alias) never drift on which flags they accept:
    # ``--project-dir`` picks the cache DB; ``--workspace`` / ``--db`` load
    # read-only multi-repo bundles; ``--project`` scopes the query to one loaded
    # project; ``--no-rust`` / ``--cache-dir`` / ``-v`` are engine/verbosity knobs.
    def _add_query_flags(sp_query: argparse.ArgumentParser) -> None:
        sp_query.add_argument(
            "--project-dir",
            dest="project",
            default=".",
            help="Path to the project root (default: current directory). "
            "Determines which cache database is loaded.",
        )
        sp_query.add_argument("--workspace", **_workspace)
        sp_query.add_argument("--db", **_db)
        sp_query.add_argument("--project", **_project_scope)
        sp_query.add_argument("--no-rust", **_no_rust)
        sp_query.add_argument("--cache-dir", **_cache_dir)
        sp_query.add_argument("-v", "--verbose", **_verbose)

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
            "--full-dep",
            action="append",
            dest="full_deps",
            default=None,
            metavar="NAME",
            help="Promote a dependency to the full project-grade pipeline (all its "
            "chunks dense-embedded, not just doc pages). Repeatable; accepts fnmatch "
            "globs. Merges into embedding.full_index_dependencies from YAML.",
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
                help="Watch the project for changes and reindex on edits.",
            )
            # Multi-repo serve: load pre-built db bundles read-only (skips
            # indexing + watch) so one MCP server hosts several indexed repos.
            sp.add_argument("--workspace", **_workspace)
            sp.add_argument("--db", **_db)

    # ``link`` is an OPERATOR action (spec 2026-07-11 §3.9): materialize or
    # refresh the workspace cross-link overlay sidecar. It takes no tuning
    # flags — all behavior (kinds, match_scope, alias resolution, scores)
    # comes from YAML via AppConfig, per the MCP-surface/YAML rule.
    sp_link = sub.add_parser(
        "link",
        help="Build/refresh cross-repo reference links for a workspace",
        description=(
            "Resolve references across the bundles of a multi-repo workspace and "
            "persist them to the pydocs-links.sqlite3 overlay next to the bundles. "
            "Serve runs this automatically at startup (reference_graph.cross_repo."
            "link_on_serve); the verb exists to pre-bake overlays into CI images / "
            "read-only deployments and for freshness gating."
        ),
    )
    sp_link.add_argument("--workspace", **_workspace)
    sp_link.add_argument("--db", **_db)
    sp_link.add_argument(
        "--check",
        action="store_true",
        help="Detection only: exit 1 if any bundle's links are stale; write nothing.",
    )
    sp_link.add_argument("-v", "--verbose", **_verbose)

    # ``search`` is the CLI face of the ``search_codebase`` MCP tool — one of
    # the six task-shaped tools (see the block below for the other five).
    sp_search = sub.add_parser(
        "search",
        help="Semantic + keyword search over project + deps",
        description=(
            "Semantic + keyword search across your project's source AND every "
            "installed dependency (docs + code); the default ranks by dense embeddings "
            "with reference-graph expansion (BM25 and hybrid presets are opt-in). "
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
        choices=["docs", "api", "any", "decision"],
        default="any",
        help="Which index to search: 'docs' = prose / README, 'api' = functions / classes, 'decision' = mined architectural decisions, 'any' = both docs+api (default).",
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
        help="Result cap for multi-repo union searches (--workspace/--db with "
        "2+ projects; 1-1000, default: 10). Single-project result count is "
        "set by the retrieval pipeline YAML, not this flag.",
    )
    _add_query_flags(sp_search)

    # ── Six task-shaped subcommands mirror the six MCP tools 1:1 (spec §D1) ──
    # Each carries the shared query flags via ``_add_query_flags``; only their
    # positional/tool-specific args differ. ``search`` above is the sixth tool
    # (``search_codebase``) — its flag set already IS the task-shaped interface.
    # Help text comes from the ``TOOL_DOCS`` single source so the CLI and MCP
    # descriptions never drift (spec §D13).
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    p_overview = sub.add_parser("overview", help=TOOL_DOCS["get_overview"].splitlines()[0])
    p_overview.add_argument("package", nargs="?", default="")
    _add_query_flags(p_overview)

    p_symbol = sub.add_parser("symbol", help=TOOL_DOCS["get_symbol"].splitlines()[0])
    p_symbol.add_argument("target")
    p_symbol.add_argument("--depth", choices=["summary", "tree", "source"], default="summary")
    _add_query_flags(p_symbol)

    p_context = sub.add_parser("context", help=TOOL_DOCS["get_context"].splitlines()[0])
    p_context.add_argument("targets", nargs="+")
    _add_query_flags(p_context)

    p_refs = sub.add_parser("refs", help=TOOL_DOCS["get_references"].splitlines()[0])
    p_refs.add_argument("target")
    p_refs.add_argument(
        "--direction",
        choices=["callers", "callees", "inherits", "impact", "governed_by"],
        default="callers",
    )
    p_refs.add_argument("--limit", type=int, default=None)
    _add_query_flags(p_refs)

    p_why = sub.add_parser("why", help=TOOL_DOCS["get_why"].splitlines()[0])
    p_why.add_argument("query", nargs="?", default="")
    p_why.add_argument(
        "--target",
        action="append",
        dest="targets",
        default=None,
        # §D11 target classification, mirrored from ``_classify_target``: a value
        # with ``/`` or a source-file extension is a path (``a/b.py``); a dotted
        # value is a qname (``pkg.mod``); a bare single token tries both.
        help=(
            "decisions affecting a target; repeatable. A path (a/b.py) or a "
            "qualified name (pkg.mod) — a value with / or a source-file "
            "extension is treated as a path, a dotted value as a qname, a bare "
            "token as both."
        ),
    )
    _add_query_flags(p_why)

    sp_lookup = sub.add_parser(
        "lookup",
        help="[deprecated] Alias for symbol/refs/context — use those directly",
        description=(
            "[deprecated] Alias for symbol/refs/context (and overview for an empty "
            "target) — use those subcommands directly. "
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
        choices=[
            "default",
            "tree",
            "callers",
            "callees",
            "inherits",
            "impact",
            "context",
            "governed_by",
        ],
        default="default",
        help=(
            "What to show: 'default' = symbol summary + immediate children (start here); "
            "'tree' = full nested subtree (use when 'default' is too shallow); "
            "'callers' = who references this — use to answer 'who uses X?'; "
            "'callees' = what this calls — use to answer 'what does X depend on?'; "
            "'inherits' = base classes / interface chain — use to answer 'what does X extend?'; "
            "'impact' = everything that transitively calls this, ranked — 'what breaks if I change X?'; "
            "'governed_by' = which mined decisions govern this symbol — 'why is X the way it is?'; "
            "'context' = dependency closure packed under a token budget — 'everything to understand X'."
        ),
    )
    _add_query_flags(sp_lookup)

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


def _load_indexing_config(args: argparse.Namespace, app_config_cls: type[AppConfig]) -> AppConfig:
    """AppConfig for an indexing run: YAML + --gpu device + --full-dep merges.

    CLI --full-dep promotions merge into the YAML-declared list. Affects the
    per-package embed tier folded into chunk hashes, so a newly promoted
    dependency re-embeds fully on this run (and only that dependency).
    """
    config = app_config_cls.load(explicit_path=getattr(args, "config", None))
    config = config.with_device(gpu=getattr(args, "gpu", False))
    return config.with_full_index_dependencies(tuple(getattr(args, "full_deps", None) or ()))


async def _run_indexing(args: argparse.Namespace) -> None:
    """Thin driver for ``index`` / ``serve`` / ``watch`` reindex passes.

    Kept as a module-level coroutine so ``_cmd_index``, ``_cmd_serve``, and
    the watch loop's ``_on_change`` callback can drive it through a single
    ``asyncio.run``. All write-side wiring lives in
    ``storage.factories.build_project_indexer`` (the composition root); the
    pass sequence (integrity sweep -> stale-model invalidation -> index ->
    FTS rebuild -> metadata stamp) lives in ``application.run_index_pass``.
    This function only resolves CLI flags into arguments for those two.
    """
    from pydocs_mcp.application import run_index_pass
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import build_project_indexer

    project, db_path = _project_and_db(args)

    # Ensure the schema exists before repositories issue queries.
    open_index_database(db_path).close()

    use_inspect = not args.no_inspect
    log.info("Project: %s (mode=%s)", project, "inspect" if use_inspect else "static")

    config = _load_indexing_config(args, AppConfig)
    # Push YAML-loaded settings into module-level slots read by
    # ``LookupInput`` validators and ``ReferenceCaptureStage``. Global side
    # effect — kept as an explicit call here, NOT hidden inside the factory.
    configure_from_app_config(config)

    # CLI flag wins over YAML; YAML wins over hard-coded fallback. Depth
    # resolution stays client-side so the factory carries no argparse
    # knowledge — undocumented defaults at the wiring layer are silent traps.
    inspect_depth = (
        args.depth if args.depth is not None else config.extraction.members.inspect_depth
    )
    bundle = build_project_indexer(
        config,
        db_path,
        use_inspect=use_inspect,
        inspect_depth=inspect_depth,
    )

    stats = await run_index_pass(
        orchestrator=bundle.orchestrator,
        indexing_service=bundle.indexing_service,
        pipeline_hash=bundle.pipeline_hash,
        project=project,
        embedding_provider=config.embedding.provider,
        embedding_model=config.embedding.model_name,
        embedding_dim=config.embedding.dim,
        force=args.force,
        include_project_source=not args.skip_project,
        include_dependencies=not args.skip_deps,
        workers=args.workers,
        check_integrity=bundle.check_integrity,
        rebuild_fts=bundle.rebuild_fts,
        stamp_metadata=bundle.stamp_metadata,
        write_aggregates=bundle.write_aggregates,
    )

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

    # File-change reindexes must NEVER inherit --force: force wipes the
    # whole cache (SQLite + .tq via IndexingService.clear_all) and re-embeds
    # project + dependencies — what the user asked for on the INITIAL pass,
    # catastrophic on every save (and in serve --watch mode, queries during
    # the re-embed window would hit an empty index). Copy the namespace so
    # the caller-driven initial pass keeps its force semantics.
    watch_args = argparse.Namespace(**vars(args))
    watch_args.force = False

    async def _on_change() -> None:
        # Reindex via the same Phase 1 helper used at startup. Cache
        # makes the no-change case <100ms (spec §2).
        try:
            await _run_indexing(watch_args)
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
        # ``gpu`` must mirror the no-watch path (``_serve_run``) — otherwise
        # `serve --watch --gpu` silently falls back to CPU query embedding.
        await asyncio.to_thread(
            run,
            db_path,
            config_path=getattr(args, "config", None),
            gpu=getattr(args, "gpu", False),
        )
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


def _query_db_path(args: argparse.Namespace) -> Path | None:
    """Single-db path for a query, or ``None`` when a workspace/--db load is used."""
    if getattr(args, "workspace", None) or getattr(args, "db_paths", None):
        return None
    return _project_and_db(args)[1]


def _build_cli_tools(args: argparse.Namespace):
    """Build the ``ToolRouter`` for a query subcommand (the CLI composition root).

    Every task-shaped subcommand loads config + configures the input-model slots
    + builds routers identically — ``surface="cli"`` picks the CLI pointer syntax
    the shared envelope resolves to. Collapsed into one helper so each ``_run_*``
    runner stays a small adapter (build tools → construct its input model → print)
    and they can't drift on how they select / load databases (``--project-dir``
    single db vs ``--workspace`` / ``--db`` read-only multi-repo).
    """
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import build_routers

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    configure_from_app_config(config)
    tools, _svcs = build_routers(
        config,
        db_path=_query_db_path(args),
        workspace=args.workspace,
        db_paths=args.db_paths,
        surface="cli",
    )
    return tools


async def _run_search(args: argparse.Namespace) -> None:
    """Mirror the MCP ``search_codebase`` tool: same router + same rendering.

    Routes to one loaded project (``--project`` / a single ``--project-dir`` db)
    or unions across a ``--workspace`` / ``--db`` multi-repo load.
    """
    from pydocs_mcp.application import SearchInput

    tools = _build_cli_tools(args)
    payload = SearchInput(
        query=args.query,
        kind=args.kind,
        package=args.package,
        scope=args.scope,
        limit=args.limit,
        project=args.project_scope,
    )
    print(await tools.search_codebase(payload))


async def _run_overview(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_overview`` tool."""
    from pydocs_mcp.application.mcp_inputs import OverviewInput

    tools = _build_cli_tools(args)
    payload = OverviewInput(package=args.package, project=args.project_scope)
    print(await tools.get_overview(payload))


async def _run_symbol(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_symbol`` tool (``--depth`` summary/tree/source)."""
    from pydocs_mcp.application.mcp_inputs import SymbolInput

    tools = _build_cli_tools(args)
    payload = SymbolInput(target=args.target, depth=args.depth, project=args.project_scope)
    print(await tools.get_symbol(payload))


async def _run_context(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_context`` tool — batched targets under one budget."""
    from pydocs_mcp.application.mcp_inputs import ContextInput

    tools = _build_cli_tools(args)
    payload = ContextInput(targets=args.targets, project=args.project_scope)
    print(await tools.get_context(payload))


async def _run_refs(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_references`` tool (``--direction`` + graph traversal)."""
    from pydocs_mcp.application.mcp_inputs import ReferencesInput

    tools = _build_cli_tools(args)
    # ``limit`` is omitted when the client didn't pass ``--limit`` so the input
    # model's YAML-wired default_factory supplies the reference-graph default —
    # no literal duplicated here (single-source-of-truth defaults).
    fields = {"target": args.target, "direction": args.direction, "project": args.project_scope}
    if args.limit is not None:
        fields["limit"] = args.limit
    print(await tools.get_references(ReferencesInput(**fields)))


async def _run_why(args: argparse.Namespace) -> None:
    """Mirror the MCP ``get_why`` tool — decision search / per-target / dashboard.

    When ``decision_capture.enabled`` (the shipped default) the router dispatches
    to the real ``DecisionService`` (query → search, ``--target`` → per-target
    cards, neither → dashboard). With capture disabled the ``NullDecisionService``
    raises ``ServiceUnavailableError`` (a typed :class:`MCPToolError`); the
    ``_run_cmd`` boundary maps it to ``Error: …`` on stderr + exit 1, exactly
    like the MCP handler's error path.
    """
    from pydocs_mcp.application.mcp_inputs import WhyInput

    tools = _build_cli_tools(args)
    payload = WhyInput(query=args.query, targets=args.targets, project=args.project_scope)
    print(await tools.get_why(payload))


# ``lookup --show`` → new-router routing. ``default``/``tree`` are get_symbol
# depths; the graph shows map 1:1 to get_references directions. ``context`` and
# empty-target ("list packages") are handled separately in ``_run_lookup``.
_ALIAS_DEPTH = {"default": "summary", "tree": "tree"}
_ALIAS_DIRECTION = frozenset({"callers", "callees", "inherits", "impact", "governed_by"})


async def _run_lookup(args: argparse.Namespace) -> None:
    """Deprecated ``lookup`` alias — warn on stderr, then delegate to the new router.

    Kept for one release so existing scripts keep working. ``--show`` maps onto
    the task-shaped tools: ``default``/``tree`` → ``get_symbol``; graph shows
    (``callers``/``callees``/``inherits``/``impact``) → ``get_references``;
    ``context`` → ``get_context``; an empty target preserves the old "list
    packages" behavior via ``get_overview``.
    """
    from pydocs_mcp.application.mcp_inputs import (
        ContextInput,
        OverviewInput,
        ReferencesInput,
        SymbolInput,
    )

    print(
        "'pydocs-mcp lookup' is deprecated — use 'pydocs-mcp symbol' "
        "(or refs/context per --show); routing there now.",
        file=sys.stderr,
    )
    tools = _build_cli_tools(args)
    project = args.project_scope

    # Empty target = "list packages" — the old lookup behavior for every --show;
    # get_overview(package="") renders that listing.
    if not args.target:
        print(await tools.get_overview(OverviewInput(package="", project=project)))
        return
    if args.show == "context":
        print(await tools.get_context(ContextInput(targets=[args.target], project=project)))
        return
    if args.show in _ALIAS_DIRECTION:
        payload = ReferencesInput(target=args.target, direction=args.show, project=project)
        print(await tools.get_references(payload))
        return
    depth = _ALIAS_DEPTH[args.show]
    print(await tools.get_symbol(SymbolInput(target=args.target, depth=depth, project=project)))


def _report_cli_failure(exc: Exception, *, verbose: bool) -> int:
    """Single source of truth for the user-facing CLI failure report.

    Under ``--verbose`` the full traceback lands on stderr (via
    ``traceback.print_exc``) AND the logger records it via
    ``log.exception``. With the default stderr-attached handler that
    duplicates the traceback — intentionally: a user who reconfigures the
    logger to a file or JSON formatter still needs the traceback there,
    and ``print_exc`` alone wouldn't reach a non-stderr handler. Without
    ``--verbose`` only the short ``Error: <msg>`` line plus a hint is
    printed, and ``log.error`` (no traceback) keeps the default
    stderr-attached logger from leaking it.

    Must be called from inside an ``except`` block — ``print_exc`` and
    ``log.exception`` read the active exception context.
    """
    print(f"Error: {exc}", file=sys.stderr)
    if verbose:
        traceback.print_exc(file=sys.stderr)
        log.exception("CLI command failed")
    else:
        print("(re-run with --verbose to see the traceback)", file=sys.stderr)
        log.error("CLI command failed: %s", exc)
    return 1


def _run_blocking(fn: Callable[[], None], *, verbose: bool) -> int:
    """Run a blocking (sync) entry point under the shared error policy.

    ``fn`` executes synchronously on the CALLER's thread — no thread hop —
    so when the caller is the main thread, Python's default SIGINT handler
    still reaches the blocking ``mcp.run`` / asyncio loops inside ``fn``
    (Python delivers SIGINT only to the main thread).
    KeyboardInterrupt is a graceful Ctrl+C shutdown, not an error: exit 0.
    """
    try:
        fn()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        return _report_cli_failure(exc, verbose=verbose)


def _run_cmd(coro: Awaitable[None], *, verbose: bool) -> int:
    """Async entry-point wrapper: run ``coro`` under the shared error policy.

    The diagnostic policy itself lives in :func:`_report_cli_failure`;
    this wrapper only owns the ``asyncio.run`` hop. Unlike
    :func:`_run_blocking` it does NOT treat KeyboardInterrupt as success —
    index/search/lookup have no long-running loop a user would Ctrl+C out
    of as a normal exit.
    """
    try:
        asyncio.run(coro)
        return 0
    except Exception as exc:
        return _report_cli_failure(exc, verbose=verbose)


def _cmd_index(args: argparse.Namespace) -> int:
    return _run_cmd(_run_indexing(args), verbose=args.verbose)


def _serve_run(
    args: argparse.Namespace,
    *,
    db_path: Path | None,
    workspace: Path | None,
    db_paths: list[Path] | None,
) -> int:
    """Run the MCP server (single-db or multi-repo) under the shared error policy.

    Kept on the main thread so the default SIGINT handler reaches the blocking
    ``mcp.run`` loop (see the no-watch rationale in ``_cmd_serve``).
    """
    from pydocs_mcp.server import run

    return _run_blocking(
        lambda: run(
            db_path,
            config_path=getattr(args, "config", None),
            gpu=getattr(args, "gpu", False),
            workspace=workspace,
            db_paths=db_paths,
        ),
        verbose=getattr(args, "verbose", False),
    )


def _cmd_serve(args: argparse.Namespace) -> int:
    # Multi-repo serve (``--workspace`` / ``--db``): the dbs are pre-built and
    # read-only, so there is NO indexing phase and no watch — jump straight to the
    # server over the loaded bundles.
    workspace = getattr(args, "workspace", None)
    db_paths = getattr(args, "db_paths", None)
    multi = workspace is not None or bool(db_paths)

    if multi:
        return _serve_run(args, db_path=None, workspace=workspace, db_paths=db_paths)

    # Phase 1 — async indexing through ``_run_cmd`` so the verbose /
    # traceback policy applies to indexing failures uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    _project, db_path = _project_and_db(args)

    from pydocs_mcp.retrieval.config import AppConfig

    # Either switch enables watch mode: the CLI flag, or the YAML key
    # (serve.watch.enabled — per-deployment opt-in). The flag cannot force
    # watching OFF when the key is true. Short-circuit keeps the flag path
    # free of a config load. Spec:
    # docs/superpowers/specs/2026-07-11-cli-mcp-docs-audit-spec.md (D3).
    watch_enabled = (
        getattr(args, "watch", False)
        or AppConfig.load(explicit_path=getattr(args, "config", None)).serve.watch.enabled
    )

    if watch_enabled:
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
        return _run_blocking(
            lambda: asyncio.run(_run_watch_loop(args, db_path=db_path)),
            verbose=args.verbose,
        )

    # Phase 2 (no-watch path) — unchanged from today.
    # ``server.run`` calls ``anyio.run(self.run_stdio_async)`` internally,
    # which starts its own event loop. Running that inside
    # ``asyncio.to_thread`` would dispatch it to a worker thread, but
    # Python only delivers SIGINT to the main thread and
    # ``asyncio.to_thread`` cannot cancel a running thread — so Ctrl+C
    # against ``pydocs-mcp serve`` would not interrupt cleanly. Run on
    # the main thread so the default SIGINT handler reaches the blocking
    # loop. The try / except mirrors ``_run_cmd``'s policy.
    return _serve_run(args, db_path=db_path, workspace=None, db_paths=None)


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
    return _run_blocking(
        lambda: asyncio.run(_run_watch_only(args)),
        verbose=args.verbose,
    )


def _cmd_link(args: argparse.Namespace) -> int:
    """The ``link`` verb (spec §3.9): full/incremental pass or ``--check``."""
    import asyncio

    from pydocs_mcp.application.workspace_linker import WorkspaceLinker, detect_stale
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.server import _bundle_handles, _open_overlay_store, _resolve_projects

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    workspace = Path(args.workspace).expanduser() if args.workspace else None
    db_paths = [Path(p).expanduser() for p in (args.db_paths or [])]
    projects, _read_only = _resolve_projects(None, workspace, db_paths)
    if len(projects) < 2:
        print("link: nothing to do — a workspace needs at least two bundles")
        return 0
    cross_cfg = config.reference_graph.cross_repo
    store, persisted = _open_overlay_store(config, workspace, db_paths)
    bundles = _bundle_handles(projects)

    async def _run() -> int:
        stamps = await store.bundle_stamps()
        stale = detect_stale(bundles, stamps)
        departed = {s.project_name for s in stamps} - {b.project for b in bundles}
        if args.check:
            if stale or departed:
                print(f"stale: {sorted(stale) or '-'}; departed: {sorted(departed) or '-'}")
                return 1
            print("cross-repo links: fresh")
            return 0
        if not persisted:
            print(
                "link: overlay location is not writable (read-only filesystem?) — "
                "nothing persisted. Serve still links in memory at startup."
            )
            return 2
        linker = WorkspaceLinker(
            bundles=bundles,
            cross_links=store,
            kinds=tuple(ReferenceKind(k) for k in cross_cfg.kinds),
            match_scope=cross_cfg.match_scope,
            alias_resolution=cross_cfg.alias_resolution,
            workspace_scores=cross_cfg.workspace_scores,
        )
        # The explicit verb is always a FULL pass (spec §3.9) — the operator
        # asked for a refresh; incremental repair is the serve path's job.
        report = await linker.link(None)
        for project in sorted({b.project for b in bundles}):
            print(
                f"{project}: scanned {report.unresolved_scanned.get(project, 0)} unresolved, "
                f"created {report.edges_created.get(project, 0)} edge(s), "
                f"{report.collisions.get(project, 0)} collision(s)"
            )
        print(
            f"alias resolved {report.alias_resolved}, ambiguous {report.alias_ambiguous}; "
            f"workspace scores: {'computed' if report.workspace_scores_computed else 'skipped'}"
            f"{'' if report.pagerank_available else ' (pagerank unavailable — [graph] extra)'}"
        )
        return 0

    return asyncio.run(_run())


def _cmd_search(args: argparse.Namespace) -> int:
    return _run_cmd(_run_search(args), verbose=args.verbose)


def _cmd_overview(args: argparse.Namespace) -> int:
    return _run_cmd(_run_overview(args), verbose=args.verbose)


def _cmd_symbol(args: argparse.Namespace) -> int:
    return _run_cmd(_run_symbol(args), verbose=args.verbose)


def _cmd_context(args: argparse.Namespace) -> int:
    return _run_cmd(_run_context(args), verbose=args.verbose)


def _cmd_refs(args: argparse.Namespace) -> int:
    return _run_cmd(_run_refs(args), verbose=args.verbose)


def _cmd_why(args: argparse.Namespace) -> int:
    return _run_cmd(_run_why(args), verbose=args.verbose)


def _cmd_lookup(args: argparse.Namespace) -> int:
    return _run_cmd(_run_lookup(args), verbose=args.verbose)


# ── Entry point ───────────────────────────────────────────────────────────


_CMD_TABLE = {
    "serve": _cmd_serve,
    "index": _cmd_index,
    "watch": _cmd_watch,
    "link": _cmd_link,
    "search": _cmd_search,
    "overview": _cmd_overview,
    "symbol": _cmd_symbol,
    "context": _cmd_context,
    "refs": _cmd_refs,
    "why": _cmd_why,
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
