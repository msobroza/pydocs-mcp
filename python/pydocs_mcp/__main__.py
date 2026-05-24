"""CLI: ``python -m pydocs_mcp {serve,index,query,api} /path/to/project``.

Each subcommand is a thin wrapper over the application-layer services
(spec §5.6, AC #9, #16):

* ``serve`` / ``index`` route through :class:`ProjectIndexer`.
* ``query`` / ``api`` route through :class:`DocsSearch` /
  :class:`ApiSearch`, rendering the top composite chunk's text.

Every ``_cmd_*`` wraps its body in ``try / except Exception`` so an
uncaught failure produces ``Error: <msg>`` on stderr and a non-zero
exit code — matches the pre-PR behaviour without letting a stray
traceback leak into a caller's output pipeline (AC #16).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
from pydocs_mcp.db import (
    cache_path_for_project,
    open_index_database,
    turboquant_path_for_project,
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

    for cmd, hlp in [("serve", "Index + start MCP"), ("index", "Index only")]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("project", nargs="?", default=".")
        # default=None so the YAML-configured inspect_depth wins when the
        # flag is absent (without this, argparse's hard-coded default
        # silently shadows ``extraction.members.inspect_depth``, mirroring
        # the F11 dead-config defect /ultrareview just removed for
        # by_extension).
        sp.add_argument(
            "--depth", type=int, default=None,
            help="Submodule scan depth (default: YAML extraction.members.inspect_depth)",
        )
        sp.add_argument("--workers", type=int, default=4, help="Parallel workers")
        sp.add_argument("--force", action="store_true", help="Clear cache, re-index all")
        sp.add_argument("--skip-project", action="store_true", help="Skip project source")
        sp.add_argument("--no-rust", **_no_rust)
        sp.add_argument(
            "--no-inspect", action="store_true",
            help="Don't import deps. Read .py files from site-packages instead. "
                 "Faster, safer, no side-effects. Uses the same parser as project source.",
        )

    # sub-PR #6: replace query/api with 2 tools matching the MCP surface.
    sp_search = sub.add_parser("search", help="Full-text search over indexed docs/code")
    sp_search.add_argument("query", help="Search terms (space-separated)")
    sp_search.add_argument(
        "--kind", choices=["docs", "api", "any"], default="any",
        help="Which index to search (default: any = both)",
    )
    sp_search.add_argument(
        "-p", "--package", default="", help="Restrict to one package",
    )
    sp_search.add_argument(
        "--scope", choices=["project", "deps", "all"], default="all",
    )
    sp_search.add_argument("--limit", type=int, default=10)
    sp_search.add_argument("--project-dir", dest="project", default=".")
    sp_search.add_argument("--no-rust", **_no_rust)

    sp_lookup = sub.add_parser(
        "lookup", help="Navigate to a specific named target (package, module, class, method)",
    )
    sp_lookup.add_argument(
        "target", nargs="?", default="",
        help="Dotted path; empty = list all indexed packages",
    )
    sp_lookup.add_argument(
        "--show",
        choices=["default", "tree", "callers", "callees", "inherits"],
        default="default",
    )
    sp_lookup.add_argument("--project-dir", dest="project", default=".")
    sp_lookup.add_argument("--no-rust", **_no_rust)

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
    log.debug("DB: %s", db_path)
    return project, db_path


# ── Subcommand handlers ───────────────────────────────────────────────────


async def _run_indexing(args: argparse.Namespace, project: Path, db_path: Path) -> None:
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
    from pydocs_mcp.storage.factories import (
        build_sqlite_plus_turboquant_uow_factory,
        check_integrity_and_repair,
    )
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository

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
    # Push YAML-loaded settings into module-level slots read by
    # ``LookupInput`` validators and ``ReferenceCaptureStage`` (sub-PR #5c
    # Task 8). Indexing uses the latter via ``ReferenceCaptureStage`` in
    # the ingestion pipeline; reads use the former.
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    configure_from_app_config(config)

    # Hybrid-search composition root: SQLite + TurboQuant under a composite
    # UoW so ``reindex_package`` writes chunks AND vectors atomically.
    # ``IndexingService`` and ``ProjectIndexer`` share one composite factory
    # so the indexing transaction spans both backends without per-service
    # branching for "is there a vector store?".
    tq_path = turboquant_path_for_project(project)
    # ``--force`` clears the SQLite cache via ``IndexingService.clear_all``;
    # the .tq sidecar lives alongside and must be cleared in lockstep,
    # otherwise stale vectors with recycled SQLite IDs collide with the
    # freshly-issued IDs on the next ``add_vectors`` call (IdMapIndex
    # rejects duplicates).
    if args.force and tq_path.exists():
        tq_path.unlink()
    uow_factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path,
        dim=config.embedding.dim, bit_width=config.embedding.bit_width,
    )
    # Cache integrity sweep — drift between SQLite and the ``.tq`` sidecar
    # (process killed mid-commit, etc.) is detected and repaired by
    # clearing ``packages.content_hash`` so the next pass re-extracts.
    # No-op on a fresh project (both counts == 0).
    #
    # Skip when ``--force`` is set: the .tq wipe above leaves vec_count=0
    # while SQLite still holds N chunks, which would log a misleading
    # "Cache integrity mismatch" warning. The subsequent ``index_project(
    # force=True)`` clears everything anyway, so the integrity check is
    # superseded by the wipe + re-extract.
    if not args.force:
        repaired = await check_integrity_and_repair(
            db_path=db_path, tq_path=tq_path,
            dim=config.embedding.dim, bit_width=config.embedding.bit_width,
        )
        if repaired:
            log.warning(
                "Cache integrity: cleared content_hash on %d package(s); "
                "they will be re-extracted this run", len(repaired),
            )

    # Detect a model rename in YAML — packages tagged with the old
    # ``embedding_model`` carry vectors that the new model cannot match
    # at query time (different vector space). Clearing ``content_hash``
    # routes them through the existing hash-skip path so the next sweep
    # re-extracts + re-embeds them under the current model. Skipped
    # under ``--force``: that path already wipes the cache wholesale.
    if not args.force:
        from dataclasses import replace as dc_replace

        from pydocs_mcp.application.indexing_service import (
            find_packages_with_stale_embeddings,
        )
        stale_pkg_names = await find_packages_with_stale_embeddings(
            uow_factory=uow_factory,
            current_model=config.embedding.model_name,
        )
        if stale_pkg_names:
            log.warning(
                "Embedding model changed; re-embedding %d package(s): %s",
                len(stale_pkg_names), ", ".join(stale_pkg_names),
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

    from pydocs_mcp.application.indexing_service import IndexingService
    indexing_service = IndexingService(uow_factory=uow_factory)

    # Build the embedder ONCE at startup so every chunk goes through the
    # same configured backend. Failing here (OptionalDepMissing) surfaces
    # the "pip install pydocs-mcp[fastembed]" hint immediately rather than
    # mid-extraction. Spec §"fail loud with actionable error".
    embedder = build_embedder(config.embedding)
    ingestion_pipeline = build_ingestion_pipeline(config, embedder=embedder)
    chunk_extractor = PipelineChunkExtractor(pipeline=ingestion_pipeline)

    ast_member = AstMemberExtractor()
    members_cfg = config.extraction.members
    # CLI flag wins over YAML; YAML wins over hard-coded fallback. This
    # mirrors the pattern for every other tunable knob — undocumented
    # defaults at the wiring layer become silent traps.
    inspect_depth = (
        args.depth if args.depth is not None
        else members_cfg.inspect_depth
    )
    member_extractor = (
        InspectMemberExtractor(
            static_fallback=ast_member, depth=inspect_depth,
            members_per_module_cap=members_cfg.members_per_module_cap,
            signature_max_chars=members_cfg.signature_max_chars,
            docstring_max_chars=members_cfg.docstring_max_chars,
        )
        if use_inspect else ast_member
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
        stats.indexed, stats.cached, stats.failed, kb,
    )


def _cmd_index(args: argparse.Namespace) -> int:
    try:
        project, db_path = _project_and_db(args)
        asyncio.run(_run_indexing(args, project, db_path))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        from pydocs_mcp.server import run

        project, db_path = _project_and_db(args)
        asyncio.run(_run_indexing(args, project, db_path))
        run(db_path, config_path=getattr(args, "config", None))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_search(args: argparse.Namespace) -> int:
    """Mirrors the MCP ``search`` tool: Pydantic input + same pipelines +
    same rendering. kind='any' runs chunks and members in parallel (§8)."""
    try:
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
        print(asyncio.run(_do_search(payload, docs_svc, api_svc)))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_lookup(args: argparse.Namespace) -> int:
    """Mirrors the MCP ``lookup`` tool — same LookupService dispatch.

    Delegates wiring to :func:`build_sqlite_lookup_service` so the CLI and
    the MCP server can never drift on which stores back ``lookup``.
    """
    try:
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
        print(asyncio.run(svc.lookup(payload)))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _pre_filter_from_package(package: str | None) -> dict | None:
    """Build the MULTIFIELD pre_filter dict for ``-p/--package``.

    Mirrors ``server.py::_normalize_pkg_filter_value``: PyPI names get
    normalised to the DB's underscore/lowercase form so ``Flask-Login``
    resolves to ``flask_login``; the ``__project__`` sentinel stays intact.
    """
    if not package:
        return None
    from pydocs_mcp.deps import normalize_package_name
    from pydocs_mcp.models import ChunkFilterField

    pkg = package if package == "__project__" else normalize_package_name(package)
    return {ChunkFilterField.PACKAGE.value: pkg}


def _print_search_response(response) -> None:
    """Preserve the pre-PR CLI behaviour: print the top composite chunk's
    text (the ``TokenBudgetStep`` output) or nothing when empty.
    """
    result = response.result
    if result is None or not result.items:
        return
    print(result.items[0].text)


# ── Entry point ───────────────────────────────────────────────────────────


_CMD_TABLE = {
    "serve":  _cmd_serve,
    "index":  _cmd_index,
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
