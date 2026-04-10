"""MCP server exposing search tools over indexed docs."""
from __future__ import annotations

import inspect
import json
import logging
import pkgutil
import sqlite3
import sys
from pathlib import Path

from pydocs_mcp.db import open_db
from pydocs_mcp.deps import normalize
from pydocs_mcp.search import search_chunks, search_symbols

log = logging.getLogger("pydocs-mcp")


def run(db_path: Path):
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    conn = open_db(db_path)
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
            parts.append("Deps: " + ", ".join(reqs[:20]))

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
                f"{(s['doc'] or '').split(chr(10))[0][:120]}"
                for s in syms
            ))

        return "\n\n".join(parts)[:30_000]

    @mcp.tool()
    def search_docs(query: str, package: str = "") -> str:
        """BM25 full-text search across docs + project code.

        Args:
            query: e.g. 'batch inference', 'authentication middleware'
            package: optional filter ('__project__' for your code)
        """
        hits = search_chunks(conn, query, pkg=package or None)
        if not hits:
            return f"No results for '{query}'."

        icon = {
            "doc": "📄", "readme": "📖", "docstring": "💡",
            "project_doc": "🏠", "project_code": "📝",
        }
        parts = [
            f"{icon.get(h['kind'], '📋')} **{h['pkg']}** / {h['heading']}\n"
            f"{h['body'][:1500]}"
            for h in hits
        ]
        return f"{len(hits)} results:\n\n" + "\n\n---\n\n".join(parts)

    @mcp.tool()
    def search_api(query: str, package: str = "") -> str:
        """Search functions/classes by name or description.

        Args:
            query: e.g. 'Router', 'predict', 'DataLoader'
            package: optional filter ('__project__' for your code)
        """
        hits = search_symbols(conn, query, pkg=package or None)
        if not hits:
            return f"No symbols matching '{query}'."

        parts = []
        for s in hits:
            block = f"### {s['kind']} {s['module']}.{s['name']}{s['signature']}"
            if s["returns"]:
                block += f"\nReturns: `{s['returns']}`"
            try:
                params = json.loads(s["params"])
                if params:
                    block += "\nParams: " + ", ".join(
                        f"`{p['name']}: {p.get('type', 'Any')}`" for p in params
                    )
            except (json.JSONDecodeError, TypeError):
                pass
            if s["doc"]:
                block += f"\n\n{s['doc'][:1200]}"
            parts.append(block)

        return "\n\n---\n\n".join(parts)

    @mcp.tool()
    def inspect_module(package: str, submodule: str = "") -> str:
        """Live-import a module to show its current API.

        Args:
            package: e.g. 'fastapi'
            submodule: e.g. 'routing' → fastapi.routing
        """
        import importlib
        target = normalize(package) + (f".{submodule}" if submodule else "")
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
                    sig = str(inspect.signature(obj))[:200]
                except (ValueError, TypeError):
                    sig = "(...)"
                doc = (inspect.getdoc(obj) or "").split("\n")[0][:150]
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
