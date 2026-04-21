"""MCP server exposing 2 consolidated tools: ``search`` and ``lookup`` (sub-PR #6).

Handlers are thin adapters over application-layer services. Per the design
spec (§4.1) each tool's description is LLM-visible prose — the copy here is
the production tool-selection prompt. Rendering lives in
:mod:`pydocs_mcp.application.formatting` and the services.

Error policy (§5.2):
- Typed :class:`MCPToolError` subclasses raise through to the MCP protocol
  — FastMCP surfaces them as structured JSON-RPC errors.
- Blanket ``try/except Exception: return "..."`` is forbidden. Unexpected
  exceptions are re-raised wrapped in :class:`ServiceUnavailableError`.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from pydocs_mcp.application import (
    LookupInput,
    LookupService,
    MCPToolError,
    SearchInput,
    ServiceUnavailableError,
)
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    ChunkFilterField,
    SearchQuery,
    SearchResponse,
    SearchScope,
)

log = logging.getLogger("pydocs-mcp")


# ── helpers ───────────────────────────────────────────────────────────────


def _scope_from_string(scope: str) -> SearchScope:
    """Map SearchInput.scope literal to the SearchScope enum."""
    return {
        "project": SearchScope.PROJECT_ONLY,
        "deps": SearchScope.DEPENDENCIES_ONLY,
        "all": SearchScope.ALL,
    }[scope]


def _normalize_pkg_filter_value(package: str) -> str:
    """PyPI names like 'Flask-Login' are stored as 'flask_login' in the DB.
    ``__project__`` is a sentinel — leave intact."""
    pkg = package.strip()
    return pkg if pkg == "__project__" else normalize_package_name(pkg)


def _build_search_query(payload: SearchInput) -> SearchQuery:
    """One SearchQuery shape works for chunks, members, or both — the
    filter-key strings overlap across ChunkFilterField and
    ModuleMemberFilterField (invariant checked by AC #25)."""
    pre_filter: dict = {ChunkFilterField.SCOPE.value: _scope_from_string(payload.scope).value}
    if payload.package:
        pre_filter[ChunkFilterField.PACKAGE.value] = _normalize_pkg_filter_value(payload.package)
    return SearchQuery(terms=payload.query, pre_filter=pre_filter)


def _render_search_response(response: SearchResponse, empty_msg: str) -> str:
    """The pipeline's TokenBudgetFormatterStage wraps the final output as a
    single composite chunk, so ``items[0].text`` is the formatted body."""
    result = response.result
    if result is None or not result.items:
        return empty_msg
    return result.items[0].text


# ── server ────────────────────────────────────────────────────────────────


def run(db_path: Path, config_path: Path | None = None) -> None:
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    from pydocs_mcp.application import (
        PackageLookupService,
        SearchApiService,
        SearchDocsService,
    )
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.storage.sqlite import (
        SqliteChunkRepository,
        SqlitePackageRepository,
    )

    config = AppConfig.load(explicit_path=config_path)
    context = build_retrieval_context(db_path, config)
    provider = context.connection_provider
    package_store = SqlitePackageRepository(provider=provider)
    chunk_store = SqliteChunkRepository(provider=provider)
    member_store = context.module_member_store
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    package_lookup = PackageLookupService(
        package_store=package_store,
        chunk_store=chunk_store,
        module_member_store=member_store,
    )
    search_docs_svc = SearchDocsService(chunk_pipeline=chunk_pipeline)
    search_api_svc = SearchApiService(member_pipeline=member_pipeline)

    # Optional services — wired if sub-PR #5 / #5b have landed. Absence is
    # surfaced to the user as ServiceUnavailableError from LookupService, not
    # as an import error at server start.
    tree_svc = None
    ref_svc = None
    try:
        from pydocs_mcp.application import DocumentTreeService  # type: ignore[attr-defined]
        # Instantiation deferred until sub-PR #5 lands its tree_store wiring.
    except ImportError:
        pass
    try:
        from pydocs_mcp.application import ReferenceService  # type: ignore[attr-defined]
    except ImportError:
        pass

    lookup_svc = LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def search(
        query: str,
        kind: str = "any",
        package: str = "",
        scope: str = "all",
        limit: int = 10,
    ) -> str:
        """Full-text search over indexed docs and code (BM25 ranked).

        Use when the user describes a topic or keyword, not a specific target.

        Params:
          query:   search terms (space-separated)
          kind:    "docs" (prose/README) | "api" (functions/classes) | "any" (default)
          package: restrict to one package (e.g. "fastapi"); "" = all; "__project__" = your code
          scope:   "project" | "deps" | "all" (default)
          limit:   1–1000, default 10

        Examples:
          search(query="batch inference", kind="docs")
          search(query="HTTPBasicAuth", kind="api")
          search(query="retry logic", package="requests")
          search(query="parser", scope="project")

        For a specific known target (package, module, class, method), use lookup.
        """
        payload = SearchInput(
            query=query, kind=kind, package=package, scope=scope, limit=limit,
        )
        try:
            return await _do_search(payload, search_docs_svc, search_api_svc)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("search failed unexpectedly")
            raise ServiceUnavailableError(f"search failed: {e}") from e

    @mcp.tool()
    async def lookup(target: str = "", show: str = "default") -> str:
        """Navigate to a specific named package/module/symbol; show its info or references.

        Use when the user names an exact target.

        Params:
          target: dotted path
            ""                                          → list all indexed packages
            "fastapi"                                   → package overview + deps
            "fastapi.routing"                           → module tree
            "fastapi.routing.APIRouter"                 → class + children
            "fastapi.routing.APIRouter.include_router"  → method details
          show: "default" | "tree" (full subtree)
                | "callers" (who calls this)
                | "callees" (what this calls)
                | "inherits" (base classes)

        Examples:
          lookup(target="")
          lookup(target="fastapi.routing.APIRouter")
          lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
          lookup(target="requests.auth.HTTPBasicAuth", show="inherits")

        For keyword/topic search, use search.
        """
        payload = LookupInput(target=target, show=show)
        try:
            return await lookup_svc.lookup(payload)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("lookup failed unexpectedly")
            raise ServiceUnavailableError(f"lookup failed: {e}") from e

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")


async def _do_search(
    payload: SearchInput,
    search_docs_svc: "SearchDocsService",
    search_api_svc: "SearchApiService",
) -> str:
    """Dispatch search by kind; returns rendered markdown."""
    query = _build_search_query(payload)
    if payload.kind == "docs":
        response = await search_docs_svc.search(query)
        return _render_search_response(response, empty_msg="No matches found.")
    if payload.kind == "api":
        response = await search_api_svc.search(query)
        return _render_search_response(response, empty_msg="No symbols found.")
    # kind == "any" — run both pipelines concurrently, concatenate rendered outputs (§8).
    chunk_resp, member_resp = await asyncio.gather(
        search_docs_svc.search(query),
        search_api_svc.search(query),
    )
    chunk_text = _render_search_response(chunk_resp, empty_msg="")
    member_text = _render_search_response(member_resp, empty_msg="")
    parts = [p for p in (chunk_text, member_text) if p]
    return "\n\n".join(parts) if parts else "No matches found."
