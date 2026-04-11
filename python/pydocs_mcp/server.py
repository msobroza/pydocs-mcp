"""MCP server exposing search tools over indexed docs."""
from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import logging
import pkgutil
import re as _re
import sys
from pathlib import Path

from pydocs_mcp.constants import (
    LIVE_DOC_MAX,
    LIVE_SIGNATURE_MAX,
    PACKAGE_DOC_LINE_MAX,
    PACKAGE_DOC_MAX,
    REQUIREMENTS_DISPLAY,
    SEARCH_DOC_DISPLAY,
    SEARCH_RESULTS_MAX,
)
from pydocs_mcp.db import open_db
from pydocs_mcp.deps import normalize
from pydocs_mcp.search import concat_context, search_chunks, search_symbols

log = logging.getLogger("pydocs-mcp")

_SUBMODULE_RE = _re.compile(r'^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$')


def _validate_submodule(submodule: str) -> bool:
    """Return True if submodule is a safe dotted identifier (or empty)."""
    return bool(_SUBMODULE_RE.match(submodule))


def run(db_path: Path):
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    conn = open_db(db_path)
    atexit.register(conn.close)
    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    def list_packages() -> str:
        """List indexed packages. '__project__' = your source code."""
        rows = conn.execute(
            "SELECT name, version, summary FROM packages ORDER BY name"
        ).fetchall()
        return "\n".join(
            f"- {r['name']} {r['version']} — {r['summary']}" for r in rows
        )

    @mcp.tool()
    def get_package_doc(package: str) -> str:
        """Full docs for a package. Use '__project__' for your own code.

        Args:
            package: e.g. 'fastapi', 'vllm', '__project__'
        """
        pkg = "__project__" if package == "__project__" else normalize(package)
        info = conn.execute(
            "SELECT * FROM packages WHERE name=?", (pkg,)
        ).fetchone()
        if not info:
            return f"'{package}' not found."

        parts = [f"# {info['name']} {info['version']}\n{info['summary']}"]
        if info["homepage"]:
            parts.append(f"Homepage: {info['homepage']}")
        reqs = json.loads(info["requires"] or "[]")
        if reqs:
            parts.append("Deps: " + ", ".join(reqs[:REQUIREMENTS_DISPLAY]))

        for r in conn.execute(
            "SELECT heading, body FROM chunks WHERE pkg=? ORDER BY id LIMIT 10",
            (pkg,),
        ):
            parts.append(f"## {r['heading']}\n{r['body']}")

        syms = conn.execute(
            "SELECT kind, name, signature, doc FROM symbols WHERE pkg=? LIMIT 30",
            (pkg,),
        ).fetchall()
        if syms:
            parts.append("## API\n" + "\n".join(
                f"- `{s['kind']} {s['name']}{s['signature']}` — "
                f"{(s['doc'] or '').split(chr(10))[0][:PACKAGE_DOC_LINE_MAX]}"
                for s in syms
            ))

        return "\n\n".join(parts)[:PACKAGE_DOC_MAX]

    @mcp.tool()
    async def search_docs(
        query: str,
        package: str = "",
        internal: bool | None = None,
        topic: str = "",
    ) -> str:
        """Search documentation and source chunks with BM25 ranking.

        Args:
            query: Search terms (space-separated words, OR logic).
            package: Restrict to a specific package name. Leave empty for all packages.
            internal: True → search only the project's own source; False → search only
                dependency packages; omit (None) → search everything.
            topic: If given, restrict to chunks whose heading contains this string.
        """
        conn = await asyncio.to_thread(open_db, db_path)
        try:
            results = await asyncio.to_thread(
                search_chunks,
                conn,
                query,
                pkg=package.strip() or None,
                internal=internal,
                topic=topic.strip() or None,
            )
        finally:
            conn.close()
        if not results:
            return "No matches found."
        return concat_context(results)

    @mcp.tool()
    async def search_api(
        query: str,
        package: str = "",
        internal: bool | None = None,
    ) -> str:
        """Search symbols (functions, classes) by name or docstring.

        Args:
            query: Name fragment or docstring keyword to search for.
            package: Restrict to a specific package name. Leave empty for all packages.
            internal: True → project symbols only; False → dependency symbols only;
                omit (None) → all symbols.
        """
        conn = await asyncio.to_thread(open_db, db_path)
        try:
            results = await asyncio.to_thread(
                search_symbols,
                conn,
                query,
                pkg=package.strip() or None,
                internal=internal,
            )
        finally:
            conn.close()
        if not results:
            return "No symbols found."
        lines = []
        for r in results[:SEARCH_RESULTS_MAX]:
            sig = f"{r['name']}{r['signature']}" if r["signature"] else r["name"]
            ret = f" -> {r['returns']}" if r["returns"] else ""
            doc = r["doc"][:SEARCH_DOC_DISPLAY] if r["doc"] else ""
            lines.append(f"**`[{r['pkg']}] {r['module']}.{sig}{ret}`** ({r['kind']})\n{doc}")
        return "\n\n---\n\n".join(lines)

    @mcp.tool()
    def inspect_module(package: str, submodule: str = "") -> str:
        """Live-import a module to show its current API.

        Args:
            package: e.g. 'fastapi'
            submodule: e.g. 'routing' → fastapi.routing
        """
        import importlib
        pkg_name = normalize(package)
        row = conn.execute("SELECT name FROM packages WHERE name=?", (pkg_name,)).fetchone()
        if not row:
            return f"'{package}' is not indexed. Use list_packages() to see available packages."
        if submodule and not _validate_submodule(submodule):
            return f"Invalid submodule '{submodule}'. Use only letters, digits, underscores, and dots."
        target = pkg_name + (f".{submodule}" if submodule else "")
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return f"Cannot import '{target}'."

        items = []
        try:
            for name, obj in inspect.getmembers(mod):
                if name.startswith("_"):
                    continue
                if not (inspect.isfunction(obj) or inspect.isclass(obj)):
                    continue
                try:
                    sig = str(inspect.signature(obj))[:LIVE_SIGNATURE_MAX]
                except (ValueError, TypeError):
                    sig = "(...)"
                doc = (inspect.getdoc(obj) or "").split("\n")[0][:LIVE_DOC_MAX]
                kind = "class" if inspect.isclass(obj) else "def"
                items.append(f"{kind} {name}{sig}\n    {doc}")
                if len(items) >= 50:
                    break
        except Exception:
            pass

        if not items and hasattr(mod, "__path__"):
            try:
                subs = [
                    s for _, s, _ in pkgutil.iter_modules(mod.__path__)
                    if not s.startswith("_")
                ]
                return f"# {target}\nSubmodules: {', '.join(subs)}"
            except Exception:
                pass

        return (f"# {target}\n\n" + "\n\n".join(items)) if items else f"No API in '{target}'."

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")
