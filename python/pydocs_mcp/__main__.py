"""CLI: python -m pydocs_mcp serve /path/to/project [--no-inspect]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pydocs_mcp._fast import RUST_AVAILABLE, disable_rust
from pydocs_mcp.db import (
    build_connection_provider,
    cache_path_for_project,
    clear_all_packages,
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.deps import discover_declared_dependencies
from pydocs_mcp.indexer import index_dependencies, index_project_source
from pydocs_mcp.server import run

log = logging.getLogger("pydocs-mcp")


def main():
    p = argparse.ArgumentParser(
        prog="pydocs-mcp",
        description="Local Python docs MCP server (optionally Rust-accelerated)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", type=Path, help="Path to pydocs-mcp.yaml")
    sub = p.add_subparsers(dest="cmd")

    _no_rust = dict(action="store_true",
                    help="Force pure-Python fallback even if Rust extension is available.")

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

    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if not args.cmd:
        p.print_help()
        return

    if getattr(args, "no_rust", False) and RUST_AVAILABLE:
        disable_rust()
        log.info("Engine: Python (Rust disabled via --no-rust)")
    else:
        log.info("Engine: %s", "Rust" if RUST_AVAILABLE else "Python")

    project = Path(getattr(args, "project", ".")).resolve()
    db_path = cache_path_for_project(project)
    log.debug("DB: %s", db_path)

    if args.cmd in ("serve", "index"):
        conn = open_index_database(db_path)

        if args.force:
            clear_all_packages(conn)
            log.info("Cache cleared")

        if not args.skip_project:
            log.info("Project: %s", project)
            index_project_source(conn, project)

        deps = discover_declared_dependencies(project)
        if deps:
            use_inspect = not args.no_inspect
            stats = index_dependencies(conn, deps, args.depth, args.workers, use_inspect)
            log.info(
                "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
                stats["indexed"], stats["cached"], stats["failed"],
                db_path.stat().st_size / 1024,
            )

        rebuild_fulltext_index(conn)
        conn.close()

        if args.cmd == "serve":
            run(db_path, config_path=getattr(args, "config", None))

    elif args.cmd in ("query", "api"):
        import asyncio

        from pydocs_mcp.models import ChunkFilterField, SearchQuery
        from pydocs_mcp.retrieval.config import (
            AppConfig,
            build_chunk_pipeline_from_config,
            build_member_pipeline_from_config,
        )
        from pydocs_mcp.retrieval.serialization import BuildContext
        from pydocs_mcp.storage.sqlite import (
            SqliteModuleMemberRepository,
            SqliteVectorStore,
        )

        config = AppConfig.load(explicit_path=getattr(args, "config", None))
        provider = build_connection_provider(db_path)
        context = BuildContext(
            connection_provider=provider,
            vector_store=SqliteVectorStore(provider=provider),
            module_member_store=SqliteModuleMemberRepository(provider=provider),
            app_config=config,
        )
        terms = " ".join(args.terms)
        pre_filter = (
            {ChunkFilterField.PACKAGE.value: args.package} if args.package else None
        )
        search_query = SearchQuery(terms=terms, pre_filter=pre_filter)

        if args.cmd == "query":
            pipeline = build_chunk_pipeline_from_config(config, context)
        else:
            pipeline = build_member_pipeline_from_config(config, context)

        state = asyncio.run(pipeline.run(search_query))
        if state.result is not None and state.result.items:
            print(state.result.items[0].text)


if __name__ == "__main__":
    main()
