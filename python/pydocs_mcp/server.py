"""MCP server exposing search tools over indexed docs.

All 5 tools are async. Long-lived-conn tools use ConnectionProvider.acquire().
search_docs / search_api run pre-built CodeRetrieverPipeline instances.
"""
from __future__ import annotations

import inspect
import logging
import pkgutil
import re as _re
import sys
from pathlib import Path

from pydocs_mcp.constants import (
    LIST_PACKAGES_MAX,
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

    # Build provider + load config + build pipelines + repositories once at startup.
    from pydocs_mcp.storage.sqlite import (
        SqliteChunkRepository,
        SqliteModuleMemberRepository,
        SqlitePackageRepository,
        SqliteVectorStore,
    )
    provider = build_connection_provider(db_path)
    config = AppConfig.load(explicit_path=config_path)
    package_repository = SqlitePackageRepository(provider=provider)
    chunk_repository = SqliteChunkRepository(provider=provider)
    member_repository = SqliteModuleMemberRepository(provider=provider)
    context = BuildContext(
        connection_provider=provider,
        vector_store=SqliteVectorStore(provider=provider),
        module_member_store=member_repository,
        app_config=config,
    )
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def list_packages() -> str:
        """List indexed packages. '__project__' = your source code."""
        packages = await package_repository.list(limit=LIST_PACKAGES_MAX)
        packages = sorted(packages, key=lambda p: p.name)
        return "\n".join(
            f"- {p.name} {p.version} — {p.summary}" for p in packages
        )

    @mcp.tool()
    async def get_package_doc(package: str) -> str:
        """Full docs for a package. Use '__project__' for your own code.

        Args:
            package: e.g. 'fastapi', 'vllm', '__project__'
        """
        pkg = "__project__" if package == "__project__" else normalize_package_name(package)
        info = await package_repository.get(pkg)
        if info is None:
            return f"'{package}' not found."

        parts = [f"# {info.name} {info.version}\n{info.summary}"]
        if info.homepage:
            parts.append(f"Homepage: {info.homepage}")
        if info.dependencies:
            parts.append(
                "Deps: " + ", ".join(info.dependencies[:REQUIREMENTS_DISPLAY])
            )

        chunks = await chunk_repository.list(
            filter={ChunkFilterField.PACKAGE.value: pkg}, limit=10,
        )
        for c in chunks:
            title = c.metadata.get(ChunkFilterField.TITLE.value, "")
            parts.append(f"## {title}\n{c.text}")

        members = await member_repository.list(
            filter={ModuleMemberFilterField.PACKAGE.value: pkg}, limit=30,
        )
        if members:
            rendered = []
            for m in members:
                md = m.metadata
                kind = md.get(ModuleMemberFilterField.KIND.value, "")
                name = md.get(ModuleMemberFilterField.NAME.value, "")
                signature = md.get("signature", "")
                docstring = str(md.get("docstring", "") or "")
                first_line = docstring.split("\n")[0][:PACKAGE_DOC_LINE_MAX]
                rendered.append(f"- `{kind} {name}{signature}` — {first_line}")
            parts.append("## API\n" + "\n".join(rendered))
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
            pkg = package.strip()
            # PyPI names like "Flask-Login" are stored as "flask_login" in the DB.
            # Normalise user input so search does not silently miss hyphenated
            # or mixed-case packages. "__project__" is a sentinel — leave intact.
            if pkg != "__project__":
                pkg = normalize_package_name(pkg)
            pre_filter[ChunkFilterField.PACKAGE.value] = pkg
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
            pkg = package.strip()
            if pkg != "__project__":
                pkg = normalize_package_name(pkg)
            pre_filter[ModuleMemberFilterField.PACKAGE.value] = pkg
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
        existing = await package_repository.get(pkg_name)
        if existing is None:
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
