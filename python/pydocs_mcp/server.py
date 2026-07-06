"""MCP server exposing the six task-shaped tools (spec §D1/§D2).

The surface is ``get_overview`` / ``search_codebase`` / ``get_symbol`` /
``get_context`` / ``get_references`` / ``get_why``. Handlers are thin adapters
that validate their pydantic input model and delegate to :class:`ToolRouter`
(which wraps every response in the shared :class:`ResponseEnvelope`). All
LLM-visible prose — per-tool descriptions and the server-level orientation —
comes from :mod:`pydocs_mcp.application.tool_docs` (``TOOL_DOCS`` /
``SERVER_INSTRUCTIONS``) so the MCP and CLI surfaces never drift.

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
    MCPToolError,
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
    from pydocs_mcp.application.null_services import NullDecisionService
    from pydocs_mcp.retrieval.config import (
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import (
        build_sqlite_lookup_service,
        build_sqlite_overview_service,
        build_sqlite_symbol_source_service,
    )

    context = build_retrieval_context(loaded.db_path, config)
    # The persisted project root feeds get_overview's entry-point detector its
    # [project.scripts] table; an empty root (read-only bundles carry none)
    # degrades to "." — parse_project_scripts returns {} for a missing file.
    project_root = Path(loaded.metadata.project_root or ".")
    return ProjectServices(
        project=loaded,
        docs=DocsSearch(chunk_pipeline=build_chunk_pipeline_from_config(config, context)),
        api=ApiSearch(member_pipeline=build_member_pipeline_from_config(config, context)),
        # ``build_sqlite_lookup_service`` owns LookupService composition so the CLI
        # and MCP server never drift on which stores back ``lookup``.
        lookup=build_sqlite_lookup_service(loaded.db_path, config=config),
        # get_symbol(depth="source") reads verbatim chunk text via its own uow;
        # ``build_sqlite_symbol_source_service`` threads the YAML line cap
        # (``symbol_source.max_lines``) so the CLI and server never drift.
        symbol_source=build_sqlite_symbol_source_service(loaded.db_path, config=config),
        # get_overview reads the §D17 structural card; the factory threads the
        # YAML caps + parses [project.scripts] from the project root.
        overview=build_sqlite_overview_service(
            loaded.db_path, project_root=project_root, config=config
        ),
        # decisions is the Null impl until the slice-3 decision layer lands.
        decisions=NullDecisionService(),
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


def build_routers(config, *, db_path=None, workspace=None, db_paths=None, surface="mcp"):
    """Resolve + validate + build the per-project services and the ``ToolRouter``.

    Shared by ``run`` (MCP server) and the CLI subcommands so both select,
    validate, and load databases identically. ``surface`` ("mcp" | "cli") picks
    the pointer syntax the shared envelope resolves to. Returns
    ``(ToolRouter, services)`` — the router fronts the six task-shaped tools over
    the multi-project ``MultiProjectSearch`` / ``MultiProjectLookup`` bodies.
    """
    from pydocs_mcp.application.envelope import ResponseEnvelope
    from pydocs_mcp.application.multi_project_search import (
        MultiProjectLookup,
        MultiProjectSearch,
    )
    from pydocs_mcp.application.tool_router import ToolRouter
    from pydocs_mcp.multirepo import validate_project_embedders
    from pydocs_mcp.storage.factories import build_freshness_probe

    projects, read_only = _resolve_projects(db_path, workspace, db_paths)
    if read_only:
        validate_project_embedders(
            projects, model=config.embedding.model_name, dim=config.embedding.dim
        )
    services = tuple(_build_project_services(p, config) for p in projects)

    # Probe facts come from the FIRST loaded project. Multi-repo per-project
    # staleness is ``get_overview`` territory — one envelope for the whole
    # router keeps every tool's freshness header from drifting.
    first = services[0].project
    probe = build_freshness_probe(
        db_path=first.db_path,
        project_root=Path(first.metadata.project_root or "."),
        enabled=config.output.envelope.enabled,
        ttl_seconds=config.output.envelope.head_check_ttl_seconds,
    )
    envelope = ResponseEnvelope(
        probe=probe,
        surface=surface,
        pointers_enabled=config.output.next_pointers.enabled,
    )
    # Body-only routers (``envelope=None``): the ``ToolRouter`` owns the single
    # envelope so every one of the six tools shares one freshness header /
    # pointer-resolution / truncation footer path.
    tools = ToolRouter(
        services=services,
        envelope=envelope,
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )
    return tools, services


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

    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    config = AppConfig.load(explicit_path=config_path)
    # ``serve --gpu`` stamps the embedder execution device onto the freshly-loaded
    # config so query-time embedding runs on CUDA. Device is excluded from every
    # pipeline hash, so this never invalidates a cache.
    config = config.with_device(gpu=gpu)
    # Push YAML-loaded settings into module-level slots read by the input-model
    # validators and ``ReferenceCaptureStage``.
    configure_from_app_config(config)

    tools, services = build_routers(config, db_path=db_path, workspace=workspace, db_paths=db_paths)
    # One capability line per project so a misconfigured dense/LI wiring stays visible.
    for svc in services:
        caps = format_capabilities(build_search_backend(config, svc.project.db_path))
        log.info("%s: %s", svc.project.name, caps)

    # Session-level scope frame surfaced to MCP clients — the single source is
    # ``SERVER_INSTRUCTIONS`` in ``application.tool_docs`` so the MCP orientation
    # and the CLI top-level help never drift.
    mcp = FastMCP("pydocs-mcp", instructions=SERVER_INSTRUCTIONS)
    _register_tools(mcp, tools)

    log.info(
        "MCP ready (%d project(s): %s)", len(services), ", ".join(s.project.name for s in services)
    )
    mcp.run(transport="stdio")


async def _run_tool(name: str, produce):
    """Shared handler error boundary (§5.2).

    Awaits ``produce()`` (the ``ToolRouter`` call), re-raising typed
    :class:`MCPToolError`s unchanged and wrapping anything else in
    :class:`ServiceUnavailableError` after logging. Factored out so every one of
    the six handlers is a one-line delegation — no per-tool try/except copy.
    """
    try:
        return await produce()
    except MCPToolError:
        raise
    except Exception as e:
        log.exception("%s failed unexpectedly", name)
        raise ServiceUnavailableError(f"{name} failed: {e}") from e


def _register_tools(mcp, tools) -> None:
    """Register the six task-shaped MCP tools on ``mcp``, delegating to ``tools``.

    Each handler is a thin adapter: validate its pydantic input model and hand
    the matching :class:`ToolRouter` call to :func:`_run_tool` (the shared error
    boundary). Split out of :func:`run` so the composition root stays flat and
    each handler reads as one unit.
    """
    from mcp.types import ToolAnnotations

    from pydocs_mcp.application.mcp_inputs import (
        ContextInput,
        OverviewInput,
        ReferencesInput,
        SearchInput,
        SymbolInput,
        WhyInput,
    )
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    def _register(fn, name: str):
        """Register a thin handler under ``name`` with ``TOOL_DOCS[name]`` as its
        LLM-visible description.

        ``description=`` is passed explicitly rather than relying on ``fn.__doc__``
        so the tool text is the ``TOOL_DOCS`` single source regardless of how
        FastMCP resolves docstrings across versions (§D13).
        """
        return mcp.tool(
            name=name,
            description=TOOL_DOCS[name],
            annotations=ToolAnnotations(
                readOnlyHint=True,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )(fn)

    async def get_overview(package: str = "", project: str = "") -> str:
        payload = OverviewInput(package=package, project=project)
        return await _run_tool("get_overview", lambda: tools.get_overview(payload))

    _register(get_overview, "get_overview")

    async def search_codebase(
        query: str,
        kind: str = "any",
        package: str = "",
        scope: str = "all",
        limit: int | None = None,
        project: str = "",
    ) -> str:
        # ``limit`` is omitted from ``SearchInput`` when the client didn't send
        # one so the model's YAML-wired ``default_factory`` supplies the default —
        # no literal duplicated here (single-source-of-truth defaults).
        fields = {
            "query": query,
            "kind": kind,
            "package": package,
            "scope": scope,
            "project": project,
        }
        if limit is not None:
            fields["limit"] = limit
        payload = SearchInput(**fields)
        return await _run_tool("search_codebase", lambda: tools.search_codebase(payload))

    _register(search_codebase, "search_codebase")

    async def get_symbol(target: str, depth: str = "summary", project: str = "") -> str:
        payload = SymbolInput(target=target, depth=depth, project=project)
        return await _run_tool("get_symbol", lambda: tools.get_symbol(payload))

    _register(get_symbol, "get_symbol")

    async def get_context(targets: list[str], project: str = "") -> str:
        payload = ContextInput(targets=targets, project=project)
        return await _run_tool("get_context", lambda: tools.get_context(payload))

    _register(get_context, "get_context")

    async def get_references(
        target: str,
        direction: str = "callers",
        limit: int | None = None,
        project: str = "",
    ) -> str:
        # Same limit-omission rule as search_codebase: absent arg lets the input
        # model apply the YAML-wired reference-graph default.
        fields = {"target": target, "direction": direction, "project": project}
        if limit is not None:
            fields["limit"] = limit
        payload = ReferencesInput(**fields)
        return await _run_tool("get_references", lambda: tools.get_references(payload))

    _register(get_references, "get_references")

    async def get_why(
        query: str = "",
        targets: list[str] | None = None,
        project: str = "",
    ) -> str:
        payload = WhyInput(query=query, targets=targets, project=project)
        return await _run_tool("get_why", lambda: tools.get_why(payload))

    _register(get_why, "get_why")
