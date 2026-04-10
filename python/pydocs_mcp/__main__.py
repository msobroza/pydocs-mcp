"""CLI: python -m pydocs_mcp serve /path/to/project [--no-inspect]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pydocs_mcp._fast import RUST_AVAILABLE
from pydocs_mcp.db import db_path_for, open_db, clear_all, rebuild_fts
from pydocs_mcp.deps import resolve
from pydocs_mcp.indexer import index_project, index_deps
from pydocs_mcp.search import search_chunks, search_symbols
from pydocs_mcp.server import run

log = logging.getLogger("pydocs-mcp")


def main():
    p = argparse.ArgumentParser(
        prog="pydocs-mcp",
        description="Local Python docs MCP server (optionally Rust-accelerated)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    for cmd, hlp in [("serve", "Index + start MCP"), ("index", "Index only")]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("project", nargs="?", default=".")
        sp.add_argument("--depth", type=int, default=1, help="Submodule scan depth")
        sp.add_argument("--workers", type=int, default=4, help="Parallel workers")
        sp.add_argument("--force", action="store_true", help="Clear cache, re-index all")
        sp.add_argument("--skip-project", action="store_true", help="Skip project source")
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

    engine = "Rust" if RUST_AVAILABLE else "Python"
    log.info("Engine: %s", engine)

    project = Path(getattr(args, "project", ".")).resolve()
    db_path = db_path_for(project)
    log.debug("DB: %s", db_path)

    if args.cmd in ("serve", "index"):
        conn = open_db(db_path)

        if args.force:
            clear_all(conn)
            log.info("Cache cleared")

        if not args.skip_project:
            log.info("Project: %s", project)
            index_project(conn, project)

        deps = resolve(project)
        if deps:
            use_inspect = not args.no_inspect
            stats = index_deps(conn, deps, args.depth, args.workers, use_inspect)
            log.info(
                "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
                stats["indexed"], stats["cached"], stats["failed"],
                db_path.stat().st_size / 1024,
            )

        rebuild_fts(conn)
        conn.close()

        if args.cmd == "serve":
            run(db_path)

    elif args.cmd in ("query", "api"):
        conn = open_db(db_path)
        q = " ".join(args.terms)

        if args.cmd == "query":
            for r in search_chunks(conn, q, pkg=args.package):
                print(f"\n{'─' * 60}")
                print(f"[{r['kind']}] {r['pkg']} → {r['heading']}")
                print(r["body"][:500])
        else:
            for s in search_symbols(conn, q, pkg=args.package):
                print(f"\n{'─' * 60}")
                print(f"{s['kind']} {s['module']}.{s['name']}{s['signature']}")
                print((s["doc"] or "—")[:300])


if __name__ == "__main__":
    main()
