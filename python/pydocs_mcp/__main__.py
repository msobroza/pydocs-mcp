"""CLI: ``python -m pydocs_mcp {serve,index,query,api} /path/to/project``.

Each subcommand is a thin wrapper over the application-layer services
(spec §5.6, AC #9, #16):

* ``serve`` / ``index`` route through :class:`IndexProjectService`.
* ``query`` / ``api`` route through :class:`SearchDocsService` /
  :class:`SearchApiService`, rendering the top composite chunk's text.

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
from pydocs_mcp.db import cache_path_for_project, open_index_database

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
        sp.add_argument("--depth", type=int, default=1, help="Submodule scan depth")
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
    """Run :class:`IndexProjectService` end-to-end for ``index`` / ``serve``.

    Kept as a module-level coroutine so both ``_cmd_index`` and
    ``_cmd_serve`` can drive it through a single ``asyncio.run`` — mirrors
    the pre-PR pattern where one event loop wrapped the whole indexing
    phase so sub-loops (async SQLite writes, ``to_thread`` extractions)
    shared the same context.
    """
    from pydocs_mcp.application import (
        ChunkExtractorAdapter,
        DependencyResolverAdapter,
        IndexProjectService,
        MemberExtractorAdapter,
    )
    from pydocs_mcp.storage.wiring import build_sqlite_indexing_service

    # Ensure the schema exists before repositories issue queries.
    open_index_database(db_path).close()

    indexing_service = build_sqlite_indexing_service(db_path)
    use_inspect = not args.no_inspect
    mode = "inspect" if use_inspect else "static"
    log.info("Project: %s (mode=%s)", project, mode)

    orchestrator = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=DependencyResolverAdapter(),
        chunk_extractor=ChunkExtractorAdapter(use_inspect=use_inspect, depth=args.depth),
        member_extractor=MemberExtractorAdapter(use_inspect=use_inspect, depth=args.depth),
    )

    if args.force:
        log.info("Cache cleared")

    stats = await orchestrator.index_project(
        project,
        force=args.force,
        include_project_source=not args.skip_project,
        workers=args.workers,
    )
    await indexing_service.chunk_store.rebuild_index()

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
            SearchApiService,
            SearchDocsService,
            SearchInput,
        )
        from pydocs_mcp.retrieval.config import (
            AppConfig,
            build_chunk_pipeline_from_config,
            build_member_pipeline_from_config,
        )
        from pydocs_mcp.retrieval.wiring import build_retrieval_context
        from pydocs_mcp.server import _do_search

        _project, db_path = _project_and_db(args)
        config = AppConfig.load(explicit_path=getattr(args, "config", None))
        context = build_retrieval_context(db_path, config)
        docs_svc = SearchDocsService(
            chunk_pipeline=build_chunk_pipeline_from_config(config, context),
        )
        api_svc = SearchApiService(
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
    """Mirrors the MCP ``lookup`` tool — same LookupService dispatch."""
    try:
        from pydocs_mcp.application import (
            LookupInput,
            LookupService,
            PackageLookupService,
        )
        from pydocs_mcp.retrieval.config import AppConfig
        from pydocs_mcp.retrieval.wiring import build_retrieval_context
        from pydocs_mcp.storage.sqlite import (
            SqliteChunkRepository,
            SqlitePackageRepository,
        )

        _project, db_path = _project_and_db(args)
        config = AppConfig.load(explicit_path=getattr(args, "config", None))
        context = build_retrieval_context(db_path, config)
        provider = context.connection_provider
        package_lookup = PackageLookupService(
            package_store=SqlitePackageRepository(provider=provider),
            chunk_store=SqliteChunkRepository(provider=provider),
            module_member_store=context.module_member_store,
        )
        svc = LookupService(package_lookup=package_lookup)  # tree/ref svc optional

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
    text (the ``TokenBudgetFormatterStage`` output) or nothing when empty.
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
