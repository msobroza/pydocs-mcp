"""MCP server exposing the nine task-shaped tools (spec §D1/§D2, contract §1).

The surface is ``get_overview`` / ``search_codebase`` / ``get_symbol`` /
``get_context`` / ``get_references`` / ``get_why`` plus the three filesystem
tools ``grep`` / ``glob`` / ``read_file``. Handlers are thin adapters
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
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from pydocs_mcp.application import (
    MCPToolError,
    ServiceUnavailableError,
)

# Shared enum vocabularies (single source: mcp_inputs). Module-level for the
# same reason as ``Annotated`` / ``Field`` above — FastMCP evals the
# handlers' stringified signatures against THIS module's globals, so typing
# the params with these aliases is what makes each tool's inputSchema
# advertise the enum values (contract §6 note 3).
from pydocs_mcp.application.mcp_inputs import (
    DepthLiteral,
    DirectionLiteral,
    KindLiteral,
    OutputModeLiteral,
    ScopeLiteral,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.types import CallToolResult
    from pydantic import BaseModel

    from pydocs_mcp.application.tool_response import ToolResponse
    from pydocs_mcp.retrieval.config import AppConfig

log = logging.getLogger("pydocs-mcp")


# ── server ────────────────────────────────────────────────────────────────


def _build_project_services(
    loaded,
    config,
    *,
    embedder=None,
    multi_vector_embedder=None,
    llm_client=None,
    ref_svc=None,
    cross_navigator=None,
):
    """Build one project's read-side service set (docs + api + lookup) from its db.

    Extracted so a single-db server and a multi-repo server share ONE per-project
    wiring — the services are constructor-injected (no globals), so building N of
    them is just calling this N times. ``embedder`` / ``multi_vector_embedder`` /
    ``llm_client`` are the process-shared, config-only instances built once by
    ``build_routers`` (N bundles must not mean N model loads — W1).
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
        build_sqlite_decision_service,
        build_sqlite_file_tools_service,
        build_sqlite_lookup_service,
        build_sqlite_overview_service,
        build_sqlite_symbol_source_service,
    )

    context = build_retrieval_context(
        loaded.db_path,
        config,
        embedder=embedder,
        multi_vector_embedder=multi_vector_embedder,
        llm_client=llm_client,
    )
    # The persisted project root feeds get_overview's entry-point detector its
    # [project.scripts] table; an empty root (read-only bundles carry none)
    # degrades to "." — parse_project_scripts returns {} for a missing file.
    project_root = Path(loaded.metadata.project_root or ".")
    # ``docs`` is composed once and shared: the search / card tools AND the real
    # ``DecisionService`` (when capture is on) rank over the same chunk pipeline.
    docs = DocsSearch(chunk_pipeline=build_chunk_pipeline_from_config(config, context))
    return ProjectServices(
        project=loaded,
        docs=docs,
        api=ApiSearch(member_pipeline=build_member_pipeline_from_config(config, context)),
        # ``build_sqlite_lookup_service`` owns LookupService composition so the CLI
        # and MCP server never drift on which stores back ``lookup``.
        lookup=build_sqlite_lookup_service(
            loaded.db_path, config=config, ref_svc=ref_svc, cross_navigator=cross_navigator
        ),
        # get_symbol(depth="source") reads verbatim chunk text via its own uow;
        # ``build_sqlite_symbol_source_service`` threads the YAML line cap
        # (``symbol_source.max_lines``) so the CLI and server never drift.
        symbol_source=build_sqlite_symbol_source_service(loaded.db_path, config=config),
        # get_overview reads the §D17 structural card; the factory threads the
        # YAML caps + parses [project.scripts] from the project root.
        overview=build_sqlite_overview_service(
            loaded.db_path, project_root=project_root, config=config
        ),
        # get_why's real backing when decision capture is on; otherwise the Null
        # impl (raises a YAML-anchored ServiceUnavailableError). One wiring
        # branch — the swap lives here, keyed by ``decision_capture.enabled``.
        decisions=(
            build_sqlite_decision_service(loaded.db_path, docs=docs, config=config)
            if config.decision_capture.enabled
            else NullDecisionService()
        ),
        # grep/glob/read_file serve THIS project's live source tree under the
        # indexer's discovery scope (contract §4.1). Unlike the overview's
        # "." fallback above, an unstamped root stays None: the filesystem
        # tools must raise the read-only-bundle error, not walk the server's
        # own cwd.
        files=build_sqlite_file_tools_service(
            loaded.db_path,
            project_root=Path(loaded.metadata.project_root)
            if loaded.metadata.project_root
            else None,
            config=config,
        ),
    )


def _resolve_projects(db_path, workspace, db_paths):
    """Resolve which projects to load + whether the load is READ-ONLY.

    A ``workspace`` dir loads every ``.db`` bundle in it; explicit ``db_paths``
    load each named bundle; both are read-only (the real source may be absent, so
    the embedder is validated and no reindex/watch happens). A single ``db_path``
    is the index-and-serve target (read-write; its embedder was just written).

    Raises ``ValueError`` if none of the three selectors was given (or
    ``db_paths`` was an empty list) — without this guard the call falls through
    to ``load_project(None)``, which raises a bare, unrelated-looking
    ``AttributeError`` ('NoneType' object has no attribute 'exists') instead of
    naming the three selection modes a caller can fix.
    """
    from pydocs_mcp.multirepo import discover_workspace, load_project

    if workspace is not None:
        return discover_workspace(workspace), True
    if db_paths:
        return [load_project(p) for p in db_paths], True
    if db_path is None:
        raise ValueError(
            "no database specified: pass one of db_path, workspace, or a non-empty db_paths"
        )
    return [load_project(db_path)], False


def _run_blocking_async(coro):
    """Run a coroutine to completion from sync OR async calling contexts.

    ``build_routers`` is sync and shared by the CLI and the MCP server, but
    tests (and future embedders) may call it from a running loop — in that
    case the coroutine runs on a private loop in a worker thread. The
    coroutine only touches its own SQLite stores (asyncio.to_thread inside),
    so a private loop is safe.
    """
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _bundle_handles(projects):
    """LoadedProjects → linker BundleHandles (read-only uow factories)."""
    from pydocs_mcp.application.workspace_linker import BundleHandle
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    return tuple(
        BundleHandle(
            project=p.name,
            bundle_stem=p.db_path.stem,
            bundle_path=str(p.db_path),
            indexed_at=p.metadata.indexed_at or 0.0,
            git_head=p.metadata.git_head,
            uow_factory=build_sqlite_uow_factory(p.db_path),
            embedding_provider=p.metadata.embedding_provider,
            embedding_model=p.metadata.embedding_model,
            embedding_dim=p.metadata.embedding_dim,
            pipeline_hash=p.metadata.pipeline_hash,
        )
        for p in projects
    )


def _build_similar_generator(config, embedder=None):
    """The §A1.2 SIMILAR generator — Null unless ``similar`` is opted in.

    ``embedder=None`` (the CLI ``link`` verb) builds the serving embedder
    lazily so the model only loads when the operator actually opted in.
    """
    from pydocs_mcp.application.similar_linker import (
        NullSimilarLinkGenerator,
        SimilarLinkGenerator,
    )

    cross_cfg = config.reference_graph.cross_repo
    if "similar" not in cross_cfg.kinds:
        return NullSimilarLinkGenerator()
    if embedder is None:
        from pydocs_mcp.retrieval.factories import build_shared_retrieval_deps

        embedder = build_shared_retrieval_deps(config)[0]
    return SimilarLinkGenerator(
        embedder=embedder,
        serving_fingerprint=(
            config.embedding.provider,
            config.embedding.model_name,
            config.embedding.dim,
        ),
        top_k=cross_cfg.similar.top_k,
        min_score=cross_cfg.similar.min_score,
    )


def _overlay_candidates(config, workspace, db_paths):
    """Ordered overlay-path candidates (spec §3.1) — pure path resolution, no I/O.

    Shared by ``_open_overlay_store`` (which probe-creates the first writable
    one) and ``link --check`` (which only tests existence, never creating —
    AC21 'writes nothing')."""
    from pydocs_mcp.storage.factories import overlay_path_for

    cross_cfg = config.reference_graph.cross_repo
    if cross_cfg.overlay_dir is not None:
        return [cross_cfg.overlay_dir / "pydocs-links.sqlite3"]
    candidates = [overlay_path_for(workspace, tuple(db_paths or ()))]
    if workspace is not None:
        # Home fallback keyed by the resolved workspace path (spec §3.1).
        import hashlib as _hashlib

        digest = _hashlib.md5(
            str(workspace.resolve()).encode("utf-8"), usedforsecurity=False
        ).hexdigest()[:10]
        candidates.append(Path("~/.pydocs-mcp/links").expanduser() / f"{digest}.sqlite3")
    return candidates


def _open_overlay_store(config, workspace, db_paths):
    """The overlay store with the §3.1/§3.8 fallback chain.

    Workspace-local → home fallback → in-memory (EROFS degradation). Returns
    ``(store, persisted)`` — ``persisted=False`` means links must be computed
    fresh each serve and one-shot CLI queries skip linking entirely (AC20).
    """
    import sqlite3 as _sqlite3

    from pydocs_mcp.storage.factories import build_cross_link_store
    from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore

    for path in _overlay_candidates(config, workspace, db_paths):
        store = build_cross_link_store(path)
        try:
            # Probe write access by ensuring the schema exists (creates the
            # file); read-only filesystems raise OperationalError here.
            _run_blocking_async(store.bundle_stamps())
            return store, True
        except (_sqlite3.OperationalError, OSError):
            continue
    log.warning("cross-link overlay unwritable in every location; using in-memory links")
    return InMemoryCrossLinkStore(), False


def _prepare_cross_links(config, projects, *, workspace, db_paths, run_link, embedder=None):
    """Compose the workspace cross-link layer (spec §3.5, §3.8, §A1.8).

    Returns ``(store, navigator, ref_services, status_line)``. Disabled or
    single-bundle → Null objects and the feature is inert (N7). ``run_link``
    is True only on the serve path; one-shot CLI queries read the persisted
    overlay with the stale-exclusion rule and never pay a link pass (AC20).
    """
    from pydocs_mcp.application.cross_repo_navigator import (
        CrossRepoNavigator,
        NullCrossRepoNavigator,
    )
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.application.workspace_linker import WorkspaceLinker, detect_stale
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory
    from pydocs_mcp.storage.null_cross_link_store import NullCrossLinkStore

    cross_cfg = config.reference_graph.cross_repo
    if not cross_cfg.enabled or len(projects) < 2:
        return NullCrossLinkStore(), NullCrossRepoNavigator(), {}, "disabled"

    store, persisted = _open_overlay_store(config, workspace, db_paths)
    bundles = _bundle_handles(projects)
    kinds = tuple(ReferenceKind(k) for k in cross_cfg.kinds)
    linker = WorkspaceLinker(
        bundles=bundles,
        cross_links=store,
        kinds=kinds,
        match_scope=cross_cfg.match_scope,
        alias_resolution=cross_cfg.alias_resolution,
        workspace_scores=cross_cfg.workspace_scores,
        similar_generator=_build_similar_generator(config, embedder),
    )

    # link_on_serve: false makes serve DETECTION-ONLY (spec §3.8): no repair
    # pass at startup, stale/departed edges excluded from reads, a warning
    # points at `pydocs-mcp link`. Default true keeps the repair-on-serve
    # behavior. One-shot CLI queries never link (run_link is False).
    should_link = run_link and cross_cfg.link_on_serve

    async def _startup():
        stamps = await store.bundle_stamps()
        stale = detect_stale(bundles, stamps)
        departed = {s.project_name for s in stamps} - {b.project for b in bundles}
        if should_link and (not persisted or stale or departed or not stamps):
            report = await linker.link(None if not stamps else stale or None)
            log.info(
                "cross-repo link pass: %s",
                {
                    "edges_created": dict(report.edges_created),
                    "alias_resolved": report.alias_resolved,
                    "alias_ambiguous": report.alias_ambiguous,
                    "collisions": dict(report.collisions),
                    "similar_edges": report.similar_edges,
                    "embedder_mismatches": report.embedder_mismatches,
                    "per_pair_similar_seconds": dict(report.per_pair_similar_seconds),
                    "pagerank_available": report.pagerank_available,
                },
            )
            return frozenset(), "fresh"
        # Detection-only path (link_on_serve false, or a one-shot CLI read):
        # exclude edges touching a stale OR departed bundle — never serve a
        # dangling cross edge into a reindexed or removed project (AC20/§3.8).
        exclude = stale | departed
        if exclude:
            log.warning(
                "cross-repo links stale/departed for %s — run `pydocs-mcp link` to "
                "refresh; their edges are excluded from reads",
                sorted(exclude),
            )
            return frozenset(exclude), "stale(" + ", ".join(sorted(exclude)) + ")"
        return frozenset(), "fresh" if stamps else "stale(unlinked)"

    if run_link or persisted:
        stale_now, status = _run_blocking_async(_startup())
    else:
        stale_now, status = frozenset(), "stale(unlinked)"
    read_store = _StaleExcludingStore(store, stale_now) if stale_now else store

    ref_services = {
        p.name: ReferenceService(
            uow_factory=build_sqlite_uow_factory(p.db_path),
            project_name=p.name,
            cross_links=read_store,
        )
        for p in projects
    }
    navigator = CrossRepoNavigator(
        services=ref_services,
        uow_factories={p.name: build_sqlite_uow_factory(p.db_path) for p in projects},
        cross_links=read_store,
        max_projects_per_walk=cross_cfg.max_projects_per_walk,
        workspace_scores=cross_cfg.workspace_scores,
    )
    return read_store, navigator, ref_services, status


class _StaleExcludingStore:
    """Read-side wrapper excluding edges that touch stale-stamped projects.

    Stale edges are never silently served (spec §3.8) — a dangling
    ``to_node_id`` into a reindexed bundle could point at a node that no
    longer exists. Writes pass through unchanged.
    """

    def __init__(self, inner, stale):
        self._inner = inner
        self._stale = frozenset(stale)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def edges_into(self, to_project, to_node_id, *, kinds=None, limit=200):
        rows = await self._inner.edges_into(to_project, to_node_id, kinds=kinds, limit=limit)
        return tuple(
            e for e in rows if e.from_project not in self._stale and e.to_project not in self._stale
        )

    async def edges_from(self, from_project, from_node_id, *, kinds=None, limit=200):
        rows = await self._inner.edges_from(from_project, from_node_id, kinds=kinds, limit=limit)
        return tuple(
            e for e in rows if e.from_project not in self._stale and e.to_project not in self._stale
        )


def build_routers(
    config, *, db_path=None, workspace=None, db_paths=None, surface="mcp", run_link_pass=False
):
    """Resolve + validate + build the per-project services and the ``ToolRouter``.

    Shared by ``run`` (MCP server) and the CLI subcommands so both select,
    validate, and load databases identically. ``surface`` ("mcp" | "cli") picks
    the pointer syntax the shared envelope resolves to. Returns
    ``(ToolRouter, services)`` — the router fronts the nine task-shaped tools over
    the multi-project ``MultiProjectSearch`` / ``MultiProjectLookup`` bodies.
    """
    import json

    from pydocs_mcp.application.envelope import ResponseEnvelope
    from pydocs_mcp.application.multi_project_search import (
        MultiProjectLookup,
        MultiProjectSearch,
    )
    from pydocs_mcp.application.tool_router import ToolRouter
    from pydocs_mcp.multirepo import validate_project_embedders
    from pydocs_mcp.retrieval.factories import build_shared_retrieval_deps
    from pydocs_mcp.storage.factories import build_freshness_probe

    projects, read_only = _resolve_projects(db_path, workspace, db_paths)
    if read_only:
        validate_project_embedders(
            projects, model=config.embedding.model_name, dim=config.embedding.dim
        )
    # Shared, config-only deps built ONCE — after the mismatch guard, so a
    # mismatched workspace fails BEFORE any model load (previously it loaded
    # the model N times and then failed). The guard is also what makes one
    # embedder instance semantically valid for every loaded project.
    embedder, multi_vector_embedder, llm_client = build_shared_retrieval_deps(config)
    if config.embedding.query_cache.enabled:
        log.debug(
            json.dumps(
                {
                    "event": "query_cache_enabled",
                    "max_entries": config.embedding.query_cache.max_entries,
                    "ttl_seconds": config.embedding.query_cache.ttl_seconds,
                    "query_identity": config.embedding.compute_query_identity_hash(),
                }
            )
        )
    _cross_links, navigator, ref_services, cross_status = _prepare_cross_links(
        config,
        projects,
        workspace=workspace,
        db_paths=db_paths,
        run_link=run_link_pass,
        embedder=embedder,
    )
    services = tuple(
        _build_project_services(
            p,
            config,
            embedder=embedder,
            multi_vector_embedder=multi_vector_embedder,
            llm_client=llm_client,
            ref_svc=ref_services.get(p.name),
            cross_navigator=navigator,
        )
        for p in projects
    )

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
        cross_link_status=cross_status if len(services) > 1 else "",
    )
    return tools, services


def _apply_descriptions_source(cli_path: Path | None, config: AppConfig) -> None:
    """Bind the winning description source and log the pinned artifact line.

    ADR 0006 §2-§4: precedence is ``--descriptions`` flag > env > user YAML
    > packaged, and an explicitly named source that is missing or invalid is
    a hard startup error (universal strictness). Called from ``run()``
    BEFORE the indexing pass (fail fast on a bad candidate — don't index for
    minutes and then die) and therefore before ``FastMCP(...)`` /
    ``_register_tools`` capture the ``tool_docs`` attributes — a
    post-registration rebind could never reach the wire, which is what keeps
    the hash equal to what is actually served. The log format is pinned by
    test: Phase 2 attribution parses it from the startup log, not the wire.
    """
    from pydocs_mcp.application.description_override import apply_descriptions_override

    artifact_hash, source = apply_descriptions_override(
        cli_path=cli_path, configured_path=config.serve.descriptions_path
    )
    log.info("descriptions artifact %s source=%s", artifact_hash[:12], source)


def run(
    db_path: Path | None = None,
    config_path: Path | None = None,
    *,
    gpu: bool = False,
    workspace: Path | None = None,
    db_paths: list[Path] | None = None,
    descriptions_path: Path | None = None,
) -> None:
    """Start the MCP server over one project (``db_path``) or several — a
    ``workspace`` dir or explicit ``db_paths`` (read-only multi-repo).
    ``descriptions_path`` is the ``--descriptions`` override document
    (highest-precedence description source, ADR 0006)."""
    from mcp.server.fastmcp import FastMCP

    from pydocs_mcp.application.mcp_inputs import configure_from_app_config
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

    _apply_descriptions_source(descriptions_path, config)

    tools, services = build_routers(
        config, db_path=db_path, workspace=workspace, db_paths=db_paths, run_link_pass=True
    )
    # One capability line per project so a misconfigured dense/LI wiring stays visible.
    for svc in services:
        caps = format_capabilities(build_search_backend(config, svc.project.db_path))
        log.info("%s: %s", svc.project.name, caps)

    # Session-level scope frame surfaced to MCP clients — the single source is
    # ``SERVER_INSTRUCTIONS`` in ``application.tool_docs`` so the MCP orientation
    # and the CLI top-level help never drift. Imported HERE (not at the top of
    # ``run``) because the import statement snapshots the attribute value — it
    # must run after ``_apply_descriptions_source`` or an override document's
    # instructions would never reach the wire.
    from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS

    mcp = FastMCP("pydocs-mcp", instructions=SERVER_INSTRUCTIONS)
    _register_tools(mcp, tools)

    log.info(
        "MCP ready (%d project(s): %s)", len(services), ", ".join(s.project.name for s in services)
    )
    mcp.run(transport="stdio")


async def _run_tool(
    name: str, produce: Callable[[], Awaitable[ToolResponse]], envelope_model: type[BaseModel]
) -> CallToolResult:
    """Shared handler error boundary (§5.2) + dual-form result assembly (§2).

    Awaits ``produce()`` (the ``ToolRouter`` call), re-raising typed
    :class:`MCPToolError`s unchanged and wrapping anything else in
    :class:`ServiceUnavailableError` after logging. The successful
    :class:`ToolResponse` is converted to a ``CallToolResult`` carrying the
    markdown text block plus ``structuredContent`` validated against the
    tool's envelope model — the conversion runs INSIDE the boundary, so a
    malformed items row surfaces as a logged ``ServiceUnavailableError``
    rather than an unlogged raw ``ValidationError``. Factored out so every
    one of the handlers is a one-line delegation — no per-tool try/except
    or conversion copy.
    """
    try:
        response = await produce()
        return _to_call_tool_result(response, envelope_model)
    except MCPToolError:
        raise
    except Exception as e:
        log.exception("%s failed unexpectedly", name)
        raise ServiceUnavailableError(f"{name} failed: {e}") from e


def _to_call_tool_result(response: ToolResponse, envelope_model: type[BaseModel]) -> CallToolResult:
    """Both wire forms of one response — FastMCP passes a ``CallToolResult``
    through unchanged after re-validating ``structuredContent`` against the
    advertised output model (mcp 1.27.1 ``func_metadata.convert_result``)."""
    from mcp.types import CallToolResult, TextContent

    structured = envelope_model.model_validate(response.structured()).model_dump(mode="json")
    return CallToolResult(
        content=[TextContent(type="text", text=response.text)],
        structuredContent=structured,
    )


def _register_tools(mcp, tools) -> None:
    """Register the nine task-shaped MCP tools on ``mcp``, delegating to ``tools``.

    Each handler is a thin adapter: validate its pydantic input model and hand
    the matching :class:`ToolRouter` call to :func:`_run_tool` (the shared error
    boundary). Split out of :func:`run` so the composition root stays flat and
    each handler reads as one unit.

    The grep flag annotations (``Annotated[..., Field(validation_alias="-i")]``)
    resolve from server.py's MODULE globals when FastMCP evals the stringified
    signatures — which is why ``Annotated`` / ``Field`` are module-level imports
    while everything else here stays function-local.
    """
    from mcp.types import CallToolResult, ToolAnnotations

    from pydocs_mcp.application.mcp_inputs import (
        ContextInput,
        OverviewInput,
        ReferencesInput,
        SearchInput,
        SymbolInput,
        WhyInput,
    )
    from pydocs_mcp.application.tool_docs import TOOL_DOCS
    from pydocs_mcp.application.tool_response import ENVELOPE_MODELS

    def _register(fn, name: str):
        """Register a thin handler under ``name`` with ``TOOL_DOCS[name]`` as its
        LLM-visible description.

        ``description=`` is passed explicitly rather than relying on ``fn.__doc__``
        so the tool text is the ``TOOL_DOCS`` single source regardless of how
        FastMCP resolves docstrings across versions (§D13).

        The return annotation is stamped as a real ``Annotated[CallToolResult,
        <EnvelopeModel>]`` object post-def: FastMCP advertises the model as the
        tool's ``outputSchema`` and passes the handler's ``CallToolResult``
        through after validating ``structuredContent`` against it. It must be
        attached dynamically because ``from __future__ import annotations``
        stringifies source annotations and FastMCP's ``eval_str`` resolution
        cannot see this function's local names.
        """
        fn.__annotations__["return"] = Annotated[CallToolResult, ENVELOPE_MODELS[name]]
        return mcp.tool(
            name=name,
            description=TOOL_DOCS[name],
            annotations=ToolAnnotations(
                readOnlyHint=True,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )(fn)

    async def get_overview(package: str = "", project: str = "") -> CallToolResult:
        payload = OverviewInput(package=package, project=project)
        return await _run_tool(
            "get_overview", lambda: tools.get_overview(payload), ENVELOPE_MODELS["get_overview"]
        )

    _register(get_overview, "get_overview")

    async def search_codebase(
        query: str,
        kind: KindLiteral = "any",
        package: str = "",
        scope: ScopeLiteral = "all",
        limit: int | None = None,
        project: str = "",
    ) -> CallToolResult:
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
        return await _run_tool(
            "search_codebase",
            lambda: tools.search_codebase(payload),
            ENVELOPE_MODELS["search_codebase"],
        )

    _register(search_codebase, "search_codebase")

    async def get_symbol(
        target: str, depth: DepthLiteral = "summary", project: str = ""
    ) -> CallToolResult:
        payload = SymbolInput(target=target, depth=depth, project=project)
        return await _run_tool(
            "get_symbol", lambda: tools.get_symbol(payload), ENVELOPE_MODELS["get_symbol"]
        )

    _register(get_symbol, "get_symbol")

    async def get_context(targets: list[str], project: str = "") -> CallToolResult:
        payload = ContextInput(targets=targets, project=project)
        return await _run_tool(
            "get_context", lambda: tools.get_context(payload), ENVELOPE_MODELS["get_context"]
        )

    _register(get_context, "get_context")

    async def get_references(
        target: str,
        direction: DirectionLiteral = "callers",
        limit: int | None = None,
        project: str = "",
    ) -> CallToolResult:
        # Same limit-omission rule as search_codebase: absent arg lets the input
        # model apply the YAML-wired reference-graph default.
        fields = {"target": target, "direction": direction, "project": project}
        if limit is not None:
            fields["limit"] = limit
        payload = ReferencesInput(**fields)
        return await _run_tool(
            "get_references",
            lambda: tools.get_references(payload),
            ENVELOPE_MODELS["get_references"],
        )

    _register(get_references, "get_references")

    async def get_why(
        query: str = "",
        targets: list[str] | None = None,
        project: str = "",
    ) -> CallToolResult:
        payload = WhyInput(query=query, targets=targets, project=project)
        return await _run_tool(
            "get_why", lambda: tools.get_why(payload), ENVELOPE_MODELS["get_why"]
        )

    _register(get_why, "get_why")

    _register_filesystem_tools(_register, tools)


def _register_filesystem_tools(register, tools) -> None:
    """Register the three filesystem tools (contract §3.7-3.9) via ``register``.

    Split from :func:`_register_tools` purely to keep each registration
    function within the complexity budget. The dash-named grep flags ride
    ``validation_alias``: the inputSchema advertises the literal wire names
    (-i/-n/-A/-B/-C) while dispatch binds the Python field names.
    ``head_limit`` / ``limit`` need no omission dance (unlike search/refs):
    None IS the model default, and the YAML-wired deployment default
    (files.*) resolves inside FileToolsService.
    """
    from pydocs_mcp.application.mcp_inputs import GlobInput, GrepInput, ReadFileInput
    from pydocs_mcp.application.tool_response import ENVELOPE_MODELS

    async def grep(
        pattern: str,
        path: str = "",
        glob: str = "",
        output_mode: OutputModeLiteral = "files_with_matches",
        case_insensitive: Annotated[bool, Field(validation_alias="-i")] = False,
        line_numbers: Annotated[bool, Field(validation_alias="-n")] = True,
        after_context: Annotated[int | None, Field(validation_alias="-A", ge=0)] = None,
        before_context: Annotated[int | None, Field(validation_alias="-B", ge=0)] = None,
        context: Annotated[int | None, Field(validation_alias="-C", ge=0)] = None,
        head_limit: int | None = None,
        multiline: bool = False,
        scope: ScopeLiteral = "project",
        project: str = "",
    ) -> CallToolResult:
        payload = GrepInput(
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            case_insensitive=case_insensitive,
            line_numbers=line_numbers,
            after_context=after_context,
            before_context=before_context,
            context=context,
            head_limit=head_limit,
            multiline=multiline,
            scope=scope,
            project=project,
        )
        return await _run_tool("grep", lambda: tools.grep(payload), ENVELOPE_MODELS["grep"])

    register(grep, "grep")

    async def glob(
        pattern: str,
        path: str = "",
        head_limit: int | None = None,
        project: str = "",
    ) -> CallToolResult:
        payload = GlobInput(pattern=pattern, path=path, head_limit=head_limit, project=project)
        return await _run_tool("glob", lambda: tools.glob(payload), ENVELOPE_MODELS["glob"])

    register(glob, "glob")

    async def read_file(
        file_path: str,
        offset: int | None = None,
        limit: int | None = None,
        project: str = "",
    ) -> CallToolResult:
        payload = ReadFileInput(file_path=file_path, offset=offset, limit=limit, project=project)
        return await _run_tool(
            "read_file", lambda: tools.read_file(payload), ENVELOPE_MODELS["read_file"]
        )

    register(read_file, "read_file")
