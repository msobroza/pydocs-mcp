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
from typing import TYPE_CHECKING

from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
from pydocs_mcp.db import cache_path_for_project, open_index_database

if TYPE_CHECKING:
    from pydocs_mcp.extraction.document_node import DocumentNode

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

    for cmd, hlp in [("query", "Search docs"), ("api", "Search symbols")]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("terms", nargs="+")
        sp.add_argument("project", nargs="?", default=".")
        sp.add_argument("-p", "--package", help="Filter to one package")
        sp.add_argument("--no-rust", **_no_rust)

    # ``tree`` — Task 28, spec §14.2. Pretty-prints the DocumentNode
    # arborescence for a package (or a single module within it) from the
    # cached SQLite ``document_trees`` table. ``project`` is a flag (not a
    # positional) so it can't be confused with the optional ``module``
    # arg — argparse can't disambiguate two nargs="?" positionals.
    tp = sub.add_parser("tree", help="Print DocumentNode arborescence")
    tp.add_argument("package", help="Package name (e.g. 'requests', '__project__')")
    tp.add_argument(
        "module", nargs="?", default=None,
        help="Optional module name — omit to print the full package tree",
    )
    tp.add_argument("--project", default=".", help="Project dir (defaults to cwd)")
    tp.add_argument("--no-rust", **_no_rust)

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

    Sub-PR #5: the sub-PR #4 legacy adapters
    (``ChunkExtractorAdapter``/``MemberExtractorAdapter``/``DependencyResolverAdapter``)
    are replaced by the strategy-based classes from :mod:`pydocs_mcp.extraction`:
    :class:`PipelineChunkExtractor` (driven by the YAML ingestion pipeline),
    :class:`InspectMemberExtractor` (with :class:`AstMemberExtractor` fallback)
    or plain :class:`AstMemberExtractor` for ``--no-inspect``, and
    :class:`StaticDependencyResolver`.
    """
    from pydocs_mcp.application import IndexProjectService
    from pydocs_mcp.extraction import (
        AstMemberExtractor,
        InspectMemberExtractor,
        PipelineChunkExtractor,
        StaticDependencyResolver,
        build_ingestion_pipeline,
    )
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.wiring import build_sqlite_indexing_service

    # Ensure the schema exists before repositories issue queries.
    open_index_database(db_path).close()

    indexing_service = build_sqlite_indexing_service(db_path)
    use_inspect = not args.no_inspect
    mode = "inspect" if use_inspect else "static"
    log.info("Project: %s (mode=%s)", project, mode)

    # Sub-PR #5: build THE ingestion pipeline once and share it across
    # project + dependency extraction. ``AppConfig.load`` honours the
    # optional ``--config`` override the CLI already passes to search /
    # serve so ingestion pipeline overrides (spec §7.3) stay consistent
    # with the rest of the config.
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    ingestion_pipeline = build_ingestion_pipeline(config)
    chunk_extractor = PipelineChunkExtractor(pipeline=ingestion_pipeline)

    ast_member = AstMemberExtractor()
    member_extractor = (
        InspectMemberExtractor(static_fallback=ast_member, depth=args.depth)
        if use_inspect else ast_member
    )

    orchestrator = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=chunk_extractor,
        member_extractor=member_extractor,
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


def _cmd_query(args: argparse.Namespace) -> int:
    try:
        from pydocs_mcp.application import SearchDocsService
        from pydocs_mcp.models import SearchQuery
        from pydocs_mcp.retrieval.config import (
            AppConfig,
            build_chunk_pipeline_from_config,
        )
        from pydocs_mcp.retrieval.wiring import build_retrieval_context

        _project, db_path = _project_and_db(args)
        config = AppConfig.load(explicit_path=getattr(args, "config", None))
        context = build_retrieval_context(db_path, config)
        pipeline = build_chunk_pipeline_from_config(config, context)
        service = SearchDocsService(chunk_pipeline=pipeline)

        pre_filter = _pre_filter_from_package(args.package)
        search_query = SearchQuery(terms=" ".join(args.terms), pre_filter=pre_filter)
        response = asyncio.run(service.search(search_query))
        _print_search_response(response)
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_api(args: argparse.Namespace) -> int:
    try:
        from pydocs_mcp.application import SearchApiService
        from pydocs_mcp.models import SearchQuery
        from pydocs_mcp.retrieval.config import (
            AppConfig,
            build_member_pipeline_from_config,
        )
        from pydocs_mcp.retrieval.wiring import build_retrieval_context

        _project, db_path = _project_and_db(args)
        config = AppConfig.load(explicit_path=getattr(args, "config", None))
        context = build_retrieval_context(db_path, config)
        pipeline = build_member_pipeline_from_config(config, context)
        service = SearchApiService(member_pipeline=pipeline)

        pre_filter = _pre_filter_from_package(args.package)
        search_query = SearchQuery(terms=" ".join(args.terms), pre_filter=pre_filter)
        response = asyncio.run(service.search(search_query))
        _print_search_response(response)
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _cmd_tree(args: argparse.Namespace) -> int:
    try:
        from pydocs_mcp.application import DocumentTreeService, NotFoundError
        from pydocs_mcp.db import build_connection_provider
        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.extraction import build_package_tree
        from pydocs_mcp.storage.sqlite import SqliteDocumentTreeStore

        _project, db_path = _project_and_db(args)
        # Ensure the schema exists so an un-indexed project gives a clean
        # "No trees" miss instead of a confusing OperationalError.
        open_index_database(db_path).close()

        provider = build_connection_provider(db_path)
        tree_store = SqliteDocumentTreeStore(provider=provider)
        service = DocumentTreeService(tree_store=tree_store)

        # Normalise PyPI names (``Flask-Login`` → ``flask_login``) so the CLI
        # matches the DB's stored form — mirrors the MCP tools' behaviour.
        raw_pkg = args.package
        pkg = raw_pkg if raw_pkg == "__project__" else normalize_package_name(raw_pkg)

        if args.module:
            try:
                tree = asyncio.run(service.get_tree(pkg, args.module))
            except NotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(_format_tree_ascii(tree))
            return 0

        modules = asyncio.run(service.list_package_modules(pkg))
        if not modules:
            print(f"No trees indexed for package '{raw_pkg}'.", file=sys.stderr)
            return 1
        root = build_package_tree(pkg, modules)
        print(_format_tree_ascii(root))
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI top-level (AC #16)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _format_tree_ascii(node: "DocumentNode") -> str:
    """Render a :class:`DocumentNode` as indented Unicode tree text.

    Deterministic pre-order walk: root on its own line, each descendant
    drawn with ``├──`` / ``└──`` connectors and ``│   `` / ``    ``
    indentation — matches spec §14.2 and the familiar ``tree`` command.
    """
    lines: list[str] = [_tree_label(node)]
    _append_subtree(node.children, prefix="", lines=lines)
    return "\n".join(lines)


def _tree_label(node: "DocumentNode") -> str:
    return f"{node.qualified_name} ({node.kind.value.upper()})"


def _append_subtree(
    children: tuple["DocumentNode", ...], *, prefix: str, lines: list[str],
) -> None:
    """Recursive helper for :func:`_format_tree_ascii`."""
    for index, child in enumerate(children):
        is_last = index == len(children) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{_tree_label(child)}")
        # "│   " keeps the vertical guide for siblings still to come;
        # "    " drops it once we've drawn the last child at this level.
        extension = "    " if is_last else "│   "
        _append_subtree(child.children, prefix=prefix + extension, lines=lines)


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
    "serve": _cmd_serve,
    "index": _cmd_index,
    "query": _cmd_query,
    "api":   _cmd_api,
    "tree":  _cmd_tree,
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
