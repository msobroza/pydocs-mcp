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
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application import (
    LookupInput,
    MCPToolError,
    SearchInput,
    ServiceUnavailableError,
)
from pydocs_mcp.application.formatting import render_top_composite
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkFilterField,
    SearchQuery,
    SearchScope,
)

if TYPE_CHECKING:
    # `_do_search` is a module-level function but the services it takes are
    # constructed inside ``run()``. Import here for the type annotations
    # without paying the cost at server start.
    from pydocs_mcp.application import ApiSearch, DocsSearch

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
    return pkg if pkg == PROJECT_PACKAGE_NAME else normalize_package_name(pkg)


def _build_search_query(payload: SearchInput) -> SearchQuery:
    """One SearchQuery shape works for chunks, members, or both — the
    filter-key strings overlap across ChunkFilterField and
    ModuleMemberFilterField (invariant checked by AC #25)."""
    pre_filter: dict = {ChunkFilterField.SCOPE.value: _scope_from_string(payload.scope).value}
    if payload.package:
        pre_filter[ChunkFilterField.PACKAGE.value] = _normalize_pkg_filter_value(payload.package)
    return SearchQuery(terms=payload.query, pre_filter=pre_filter)


# ── server ────────────────────────────────────────────────────────────────


def run(db_path: Path, config_path: Path | None = None, *, gpu: bool = False) -> None:
    """Start the MCP server."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    from pydocs_mcp.application import (
        ApiSearch,
        DocsSearch,
    )
    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    config = AppConfig.load(explicit_path=config_path)
    # ``serve --gpu`` stamps the embedder execution device onto the
    # freshly-loaded config so query-time embedding runs on CUDA. Device is
    # excluded from every pipeline hash, so this never invalidates a cache.
    config = config.with_device(gpu=gpu)
    # Push YAML-loaded settings into module-level slots read by
    # ``LookupInput`` validators and ``ReferenceCaptureStage`` (sub-PR #5c
    # Task 8). One call covers both — see ``configure_from_app_config``.
    configure_from_app_config(config)
    context = build_retrieval_context(db_path, config)

    # Visibility for #64: log which retrieval capabilities the configured
    # backend actually serves so a misconfigured dense/LI wiring can't stay
    # silent. Building the backend a second time here is cheap (no I/O until a
    # query); ``build_retrieval_context`` already built one internally.
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    log.info(format_capabilities(build_search_backend(config, db_path)))

    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    search_docs_svc = DocsSearch(chunk_pipeline=chunk_pipeline)
    search_api_svc = ApiSearch(member_pipeline=member_pipeline)

    # LookupService composition is owned by ``build_sqlite_lookup_service``
    # so the CLI (``__main__._cmd_lookup``) and MCP server can never drift
    # on which stores back ``lookup``. Post-#5c (Task 8): the factory wires
    # a real ``ReferenceService`` into ``ref_svc`` — previously this site
    # constructed ``LookupService(ref_svc=None)`` inline, leaving the MCP
    # ``lookup(show="callers"|"callees"|"inherits")`` modes raising
    # ``ServiceUnavailableError`` in production.
    lookup_svc = build_sqlite_lookup_service(db_path, config=config)

    # Session-level scope frame surfaced to MCP clients. Tells the AI when
    # to reach for pydocs-mcp (installed libraries, user's project code
    # under the ``__project__`` sentinel, call graph) versus other tools,
    # and pins the fixed 2-tool surface so the AI doesn't try to synthesize
    # list_packages / get_doc handlers that don't exist.
    mcp = FastMCP(
        "pydocs-mcp",
        instructions="""pydocs-mcp indexes your current project's source code AND every installed dependency into a local hybrid (BM25 + dense embeddings) index. Use this server before web search whenever the user asks about: an installed library's API, a function/class in their own project, who-calls-what / call graph navigation, or `__project__` modules. The surface is two tools only — `search` and `lookup` — pick `search` for keyword/topic queries and `lookup` for known dotted paths or reference-graph traversal. Do NOT use for: refactoring, writing new code from scratch, runtime debugging, or libraries that aren't installed in this project (use Context7 or web search for those).""",
    )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def search(
        query: str,
        kind: str = "any",
        package: str = "",
        scope: str = "all",
        limit: int = 10,
    ) -> str:
        """Hybrid keyword + semantic search across your project's source AND every installed dependency (docs + code).

        When to use this tool:
          - Topic, keyword, concept, or partial name (you don't know the exact dotted path)
          - "How do I do X" / "Where is the code for X" style questions
          - Use `lookup` instead if you know the exact dotted path OR want to walk the code graph

        Params:
          query:   search terms (space-separated; both prose and identifiers work)
          kind:    "docs" (prose / README chunks) | "api" (functions / classes) | "any" (default)
          package: restrict to one package (e.g. "fastapi"). Use "__project__" for the USER's
                   code, not a library. "" = all packages.
          scope:   "project" (user's code only) | "deps" (installed deps only) | "all" (default).
                   Use scope="project" or package="__project__" when the user asks about THEIR
                   code, not a library — this is the most common routing mistake to avoid.
          limit:   max results 1-1000, default 10.

        Examples:
          search(query="batch inference", kind="docs")
          search(query="HTTPBasicAuth", kind="api")
          search(query="retry logic", package="requests")
          search(query="our parser", scope="project")
          search(query="ValidationError", package="__project__")

        Returns markdown with up to `limit` ranked hits, each block carrying
        package, module path, and a code/docs excerpt.
        """
        payload = SearchInput(
            query=query,
            kind=kind,
            package=package,
            scope=scope,
            limit=limit,
        )
        try:
            return await _do_search(payload, search_docs_svc, search_api_svc)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("search failed unexpectedly")
            raise ServiceUnavailableError(f"search failed: {e}") from e

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def lookup(target: str = "", show: str = "default") -> str:
        """Navigate to a known symbol (dotted path) and optionally traverse its reference graph — callers, callees, base classes.

        When to use this tool:
          - You know the exact dotted path of a package / module / class / method
          - You want to walk the code graph from a known symbol (who calls X, what X calls)
          - Use `search` instead if you only have a keyword, topic, or partial name

        Params:
          target: dotted path
            ""                                          → list all indexed packages
            "fastapi"                                   → package overview + deps
            "fastapi.routing"                           → module tree
            "fastapi.routing.APIRouter"                 → class + children
            "fastapi.routing.APIRouter.include_router"  → method details
            "__project__.my_module.MyClass"             → YOUR class (not a library)

          show:
            "default"  → symbol summary + immediate children (start here)
            "tree"     → full nested subtree (use when "default" is too shallow)
            "callers"  → every site that calls/references this symbol — use to answer "who uses X?"
            "callees"  → every symbol this calls — use to answer "what does X depend on?"
            "inherits" → base classes and interface chain — use to answer "what does X extend?"

        Examples:
          lookup(target="")
          lookup(target="fastapi.routing.APIRouter")
          lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
          lookup(target="requests.auth.HTTPBasicAuth", show="inherits")

        Returns markdown — exact shape varies by `show` mode (a summary block
        for "default", a tree for "tree", a list of caller / callee entries
        for the graph modes).
        """
        payload = LookupInput(target=target, show=show)
        try:
            return await lookup_svc.lookup(payload)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("lookup failed unexpectedly")
            raise ServiceUnavailableError(f"lookup failed: {e}") from e

    # TODO(follow-up): wire TreeService + build_package_tree
    # into ``lookup(kind="tree")`` dispatch so the tree arborescence is reachable
    # via the unified 2-tool MCP surface. Standalone ``get_document_tree`` /
    # ``get_package_tree`` handlers were removed during the rebase onto #6's
    # consolidated MCP surface; the underlying services
    # (``TreeService``, ``build_package_tree``, ``flatten_to_chunks``)
    # remain available for the integration.

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")


async def _do_search(
    payload: SearchInput,
    search_docs_svc: DocsSearch,
    search_api_svc: ApiSearch,
) -> str:
    """Dispatch search by kind; returns rendered markdown."""
    query = _build_search_query(payload)
    if payload.kind == "docs":
        response = await search_docs_svc.search(query)
        return render_top_composite(response, empty_msg="No matches found.")
    if payload.kind == "api":
        response = await search_api_svc.search(query)
        return render_top_composite(response, empty_msg="No symbols found.")
    # kind == "any" — run both pipelines concurrently, concatenate rendered outputs (§8).
    # Pass empty_msg="" so an empty half does NOT inject a "No matches" line
    # into the joined output — the final fallback below handles the all-empty case.
    chunk_resp, member_resp = await asyncio.gather(
        search_docs_svc.search(query),
        search_api_svc.search(query),
    )
    chunk_text = render_top_composite(chunk_resp, empty_msg="")
    member_text = render_top_composite(member_resp, empty_msg="")
    parts = [p for p in (chunk_text, member_text) if p]
    return "\n\n".join(parts) if parts else "No matches found."
