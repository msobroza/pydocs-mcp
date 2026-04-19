"""MCP server exposing search tools over indexed docs.

All 5 tools are async. Long-lived-conn tools use ConnectionProvider.acquire().
search_docs / search_api run pre-built CodeRetrieverPipeline instances.
"""
from __future__ import annotations

import asyncio
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
)
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import ChunkFilterField, ModuleMemberFilterField, SearchQuery, SearchScope
from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.serialization import BuildContext

log = logging.getLogger("pydocs-mcp")

_SUBMODULE_RE = _re.compile(r'^([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?$')


def _validate_submodule(submodule: str) -> bool:
    """Return True if submodule is a safe dotted identifier (or empty)."""
    return bool(_SUBMODULE_RE.match(submodule))


def _scope_from_internal(internal: bool | None) -> SearchScope:
    """Tri-state conversion of the MCP `internal` flag to a SearchScope."""
    if internal is True:
        return SearchScope.PROJECT_ONLY
    if internal is False:
        return SearchScope.DEPENDENCIES_ONLY
    return SearchScope.ALL


def run(db_path: Path, config_path: Path | None = None):
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    # Build provider + load config + build pipelines once at startup.
    provider = build_connection_provider(db_path)
    config = AppConfig.load(explicit_path=config_path)
    context = BuildContext(connection_provider=provider)
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def list_packages() -> str:
        """List indexed packages. '__project__' = your source code."""
        async with provider.acquire() as connection:
            rows = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT name, version, summary FROM packages ORDER BY name"
                ).fetchall()
            )
        return "\n".join(
            f"- {r['name']} {r['version']} — {r['summary']}" for r in rows
        )

    @mcp.tool()
    async def get_package_doc(package: str) -> str:
        """Full docs for a package. Use '__project__' for your own code.

        Args:
            package: e.g. 'fastapi', 'vllm', '__project__'
        """
        pkg = "__project__" if package == "__project__" else normalize_package_name(package)
        async with provider.acquire() as connection:
            info = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT * FROM packages WHERE name=?", (pkg,)
                ).fetchone()
            )
            if not info:
                return f"'{package}' not found."

            parts = [f"# {info['name']} {info['version']}\n{info['summary']}"]
            if info["homepage"]:
                parts.append(f"Homepage: {info['homepage']}")
            deps = json.loads(info["dependencies"] or "[]")
            if deps:
                parts.append("Deps: " + ", ".join(deps[:REQUIREMENTS_DISPLAY]))

            chunks = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT title, text FROM chunks WHERE package=? ORDER BY id LIMIT 10",
                    (pkg,),
                ).fetchall()
            )
            for r in chunks:
                parts.append(f"## {r['title']}\n{r['text']}")

            members = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT kind, name, signature, docstring "
                    "FROM module_members WHERE package=? LIMIT 30",
                    (pkg,),
                ).fetchall()
            )
        if members:
            parts.append("## API\n" + "\n".join(
                f"- `{s['kind']} {s['name']}{s['signature']}` — "
                f"{(s['docstring'] or '').split(chr(10))[0][:PACKAGE_DOC_LINE_MAX]}"
                for s in members
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
        scope = _scope_from_internal(internal)
        pre_filter: dict = {ChunkFilterField.SCOPE.value: scope.value}
        if package.strip():
            pre_filter[ChunkFilterField.PACKAGE.value] = package.strip()
        if topic.strip():
            pre_filter[ChunkFilterField.TITLE.value] = topic.strip()
        search_query = SearchQuery(terms=query, pre_filter=pre_filter)
        try:
            state = await chunk_pipeline.run(search_query)
        except Exception:
            log.warning("search_docs failed", exc_info=True)
            return "No matches found."
        if state.result is None or not state.result.items:
            return "No matches found."
        # Final item is the composite formatted chunk
        return state.result.items[0].text

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
        scope = _scope_from_internal(internal)
        pre_filter: dict = {ChunkFilterField.SCOPE.value: scope.value}
        if package.strip():
            pre_filter[ModuleMemberFilterField.PACKAGE.value] = package.strip()
        search_query = SearchQuery(terms=query, pre_filter=pre_filter)
        try:
            state = await member_pipeline.run(search_query)
        except Exception:
            log.warning("search_api failed", exc_info=True)
            return "No symbols found."
        if state.result is None or not state.result.items:
            return "No symbols found."
        return state.result.items[0].text

    @mcp.tool()
    async def inspect_module(package: str, submodule: str = "") -> str:
        """Live-import a module to show its current API.

        Args:
            package: e.g. 'fastapi'
            submodule: e.g. 'routing' → fastapi.routing
        """
        import importlib
        pkg_name = normalize_package_name(package)
        async with provider.acquire() as connection:
            row = await asyncio.to_thread(
                lambda: connection.execute(
                    "SELECT name FROM packages WHERE name=?", (pkg_name,)
                ).fetchone()
            )
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
