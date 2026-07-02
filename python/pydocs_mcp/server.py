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

import logging
from pathlib import Path

from pydocs_mcp.application import (
    LookupInput,
    MCPToolError,
    SearchInput,
    ServiceUnavailableError,
)

log = logging.getLogger("pydocs-mcp")


# ── server ────────────────────────────────────────────────────────────────


def _build_project_services(loaded, config):
    """Build one project's read-side service set (docs + api + lookup) from its db.

    Extracted so a single-db server and a multi-repo server share ONE per-project
    wiring — the services are constructor-injected (no globals), so building N of
    them is just calling this N times.
    """
    from pydocs_mcp.application import ApiSearch, DocsSearch
    from pydocs_mcp.application.multi_project_search import ProjectServices
    from pydocs_mcp.retrieval.config import (
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import build_sqlite_lookup_service

    context = build_retrieval_context(loaded.db_path, config)
    return ProjectServices(
        project=loaded,
        docs=DocsSearch(chunk_pipeline=build_chunk_pipeline_from_config(config, context)),
        api=ApiSearch(member_pipeline=build_member_pipeline_from_config(config, context)),
        # ``build_sqlite_lookup_service`` owns LookupService composition so the CLI
        # and MCP server never drift on which stores back ``lookup``.
        lookup=build_sqlite_lookup_service(loaded.db_path, config=config),
    )


def _resolve_projects(db_path, workspace, db_paths):
    """Resolve which projects to load + whether the load is READ-ONLY.

    A ``workspace`` dir loads every ``.db`` bundle in it; explicit ``db_paths``
    load each named bundle; both are read-only (the real source may be absent, so
    the embedder is validated and no reindex/watch happens). A single ``db_path``
    is the index-and-serve target (read-write; its embedder was just written).
    """
    from pydocs_mcp.multirepo import discover_workspace, load_project

    if workspace is not None:
        return discover_workspace(workspace), True
    if db_paths:
        return [load_project(p) for p in db_paths], True
    return [load_project(db_path)], False


def build_routers(config, *, db_path=None, workspace=None, db_paths=None):
    """Resolve + validate + build the per-project services and the two routers.

    Shared by ``run`` (MCP server) and the CLI ``search`` / ``lookup`` commands so
    both select, validate, and load databases identically. Returns
    ``(search_router, lookup_router, services)``.
    """
    from pydocs_mcp.application.multi_project_search import (
        MultiProjectLookup,
        MultiProjectSearch,
    )
    from pydocs_mcp.multirepo import validate_project_embedders

    projects, read_only = _resolve_projects(db_path, workspace, db_paths)
    if read_only:
        validate_project_embedders(
            projects, model=config.embedding.model_name, dim=config.embedding.dim
        )
    services = tuple(_build_project_services(p, config) for p in projects)
    return MultiProjectSearch(services=services), MultiProjectLookup(services=services), services


def run(
    db_path: Path | None = None,
    config_path: Path | None = None,
    *,
    gpu: bool = False,
    workspace: Path | None = None,
    db_paths: list[Path] | None = None,
) -> None:
    """Start the MCP server over one project (``db_path``) or several — a
    ``workspace`` dir or explicit ``db_paths`` (read-only multi-repo)."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    config = AppConfig.load(explicit_path=config_path)
    # ``serve --gpu`` stamps the embedder execution device onto the freshly-loaded
    # config so query-time embedding runs on CUDA. Device is excluded from every
    # pipeline hash, so this never invalidates a cache.
    config = config.with_device(gpu=gpu)
    # Push YAML-loaded settings into module-level slots read by ``LookupInput`` /
    # ``SearchInput`` validators and ``ReferenceCaptureStage``.
    configure_from_app_config(config)

    search_router, lookup_router, services = build_routers(
        config, db_path=db_path, workspace=workspace, db_paths=db_paths
    )
    # One capability line per project so a misconfigured dense/LI wiring stays visible.
    for svc in services:
        caps = format_capabilities(build_search_backend(config, svc.project.db_path))
        log.info("%s: %s", svc.project.name, caps)

    # Session-level scope frame surfaced to MCP clients. Tells the AI when
    # to reach for pydocs-mcp (installed libraries, user's project code
    # under the ``__project__`` sentinel, call graph) versus other tools,
    # and pins the fixed 2-tool surface so the AI doesn't try to synthesize
    # list_packages / get_doc handlers that don't exist.
    mcp = FastMCP(
        "pydocs-mcp",
        instructions="""pydocs-mcp indexes your current project's source code AND every installed dependency into a local hybrid index (dense embeddings + BM25 + a reference graph). Use this server before web search whenever the user asks about: an installed library's API, a function/class in their own project, who-calls-what / call graph navigation, or `__project__` modules. The surface is two tools only — `search` and `lookup` — pick `search` for semantic/keyword/topic queries (default retrieval is dense embeddings with reference-graph expansion) and `lookup` for known dotted paths or reference-graph traversal. Do NOT use for: refactoring, writing new code from scratch, runtime debugging, or libraries that aren't installed in this project (use Context7 or web search for those).""",
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
        project: str = "",
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
          project: when this server hosts several indexed repos, restrict to one by name
                   (e.g. "backend"). "" = search all loaded repos (results deduped). No effect
                   on a single-repo server.
          limit:   max results 1-1000, default 10.

        Examples:
          search(query="batch inference", kind="docs")
          search(query="HTTPBasicAuth", kind="api")
          search(query="retry logic", package="requests")
          search(query="our parser", scope="project")
          search(query="ValidationError", package="__project__")
          search(query="db pool", project="backend")

        Returns markdown with up to `limit` ranked hits, each block carrying
        package, module path, and a code/docs excerpt.
        """
        payload = SearchInput(
            query=query,
            kind=kind,
            package=package,
            scope=scope,
            limit=limit,
            project=project,
        )
        try:
            return await search_router.search(payload)
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
    async def lookup(target: str = "", show: str = "default", project: str = "") -> str:
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
            "impact"   → everything that transitively calls this symbol, ranked — use to answer "what breaks if I change X?"
            "context"  → the symbol's dependency closure packed under a token budget (full source + signatures + outline) — use to answer "everything I need to understand X"

        Examples:
          lookup(target="")
          lookup(target="fastapi.routing.APIRouter")
          lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
          lookup(target="requests.auth.HTTPBasicAuth", show="inherits")
          lookup(target="fastapi.routing.APIRouter.include_router", show="impact")
          lookup(target="fastapi.routing.APIRouter.include_router", show="context")
          lookup(target="app.db.Pool", project="backend")

        When this server hosts several indexed repos, pass `project` (e.g.
        "backend") to resolve the target inside one repo; "" resolves across all
        loaded repos. No effect on a single-repo server.

        Returns markdown — exact shape varies by `show` mode (a summary block
        for "default", a tree for "tree", a list of caller / callee entries
        for the graph modes).
        """
        payload = LookupInput(target=target, show=show, project=project)
        try:
            return await lookup_router.lookup(payload)
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

    log.info(
        "MCP ready (%d project(s): %s)", len(services), ", ".join(s.project.name for s in services)
    )
    mcp.run(transport="stdio")
