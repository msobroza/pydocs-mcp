"""MCP server exposing search tools over indexed docs.

Handlers are thin adapters over application-layer services (spec §5.1,
AC #7): ``PackageLookupService``, ``SearchDocsService``, ``SearchApiService``,
``ModuleIntrospectionService``. All rendering + filter-dict construction is
kept in this module so the services stay transport-agnostic.

Byte-parity with pre-PR tool I/O is a hard requirement (AC #8) — the
``_render_*`` helpers below are the single source of truth for handler
output shape.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydocs_mcp.application.module_introspection_service import (
    _validate_submodule,  # noqa: F401 — re-exported for tests/security-sensitive callers
)
from pydocs_mcp.constants import (
    LIST_PACKAGES_MAX,
    PACKAGE_DOC_LINE_MAX,
    PACKAGE_DOC_MAX,
    REQUIREMENTS_DISPLAY,
)
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    PackageDoc,
    SearchQuery,
    SearchResponse,
    SearchScope,
)

log = logging.getLogger("pydocs-mcp")


def _scope_from_internal(internal: bool | None) -> SearchScope:
    """Tri-state conversion of the MCP ``internal`` flag to a :class:`SearchScope`."""
    if internal is True:
        return SearchScope.PROJECT_ONLY
    if internal is False:
        return SearchScope.DEPENDENCIES_ONLY
    return SearchScope.ALL


def _normalize_pkg_filter_value(package: str) -> str:
    """Normalise a user-supplied package name for DB-side filter matching.

    PyPI names like ``Flask-Login`` are stored as ``flask_login`` in the DB.
    ``__project__`` is a sentinel — leave intact.
    """
    pkg = package.strip()
    return pkg if pkg == "__project__" else normalize_package_name(pkg)


def _build_chunk_query(
    query: str, package: str, internal: bool | None, topic: str,
) -> SearchQuery:
    pre_filter: dict = {ChunkFilterField.SCOPE.value: _scope_from_internal(internal).value}
    if package.strip():
        pre_filter[ChunkFilterField.PACKAGE.value] = _normalize_pkg_filter_value(package)
    if topic.strip():
        pre_filter[ChunkFilterField.TITLE.value] = topic.strip()
    return SearchQuery(terms=query, pre_filter=pre_filter)


def _build_member_query(
    query: str, package: str, internal: bool | None,
) -> SearchQuery:
    pre_filter: dict = {ChunkFilterField.SCOPE.value: _scope_from_internal(internal).value}
    if package.strip():
        pre_filter[ModuleMemberFilterField.PACKAGE.value] = _normalize_pkg_filter_value(package)
    return SearchQuery(terms=query, pre_filter=pre_filter)


def _render_search_response_chunks(response: SearchResponse) -> str:
    """Render the :class:`SearchDocsService` response. The pipeline's
    :class:`TokenBudgetFormatterStage` wraps the final output as a single
    composite chunk, so ``items[0].text`` is the formatted body."""
    result = response.result
    if result is None or not result.items:
        return "No matches found."
    return result.items[0].text


def _render_search_response_members(response: SearchResponse) -> str:
    """Render the :class:`SearchApiService` response — same composite-chunk
    contract as :func:`_render_search_response_chunks`."""
    result = response.result
    if result is None or not result.items:
        return "No symbols found."
    return result.items[0].text


def _render_package_doc(doc: PackageDoc) -> str:
    """Rebuild the pre-PR ``get_package_doc`` return string from a typed doc.

    Byte-parity contract (AC #8): blocks are joined with ``"\\n\\n"`` and the
    whole payload is truncated to :data:`PACKAGE_DOC_MAX` characters.
    """
    pkg = doc.package
    parts = [f"# {pkg.name} {pkg.version}\n{pkg.summary}"]
    if pkg.homepage:
        parts.append(f"Homepage: {pkg.homepage}")
    if pkg.dependencies:
        parts.append("Deps: " + ", ".join(pkg.dependencies[:REQUIREMENTS_DISPLAY]))

    for c in doc.chunks:
        title = c.metadata.get(ChunkFilterField.TITLE.value, "")
        parts.append(f"## {title}\n{c.text}")

    if doc.members:
        rendered: list[str] = []
        for m in doc.members:
            md = m.metadata
            kind = md.get(ModuleMemberFilterField.KIND.value, "")
            name = md.get(ModuleMemberFilterField.NAME.value, "")
            signature = md.get("signature", "")
            docstring = str(md.get("docstring", "") or "")
            first_line = docstring.split("\n")[0][:PACKAGE_DOC_LINE_MAX]
            rendered.append(f"- `{kind} {name}{signature}` — {first_line}")
        parts.append("## API\n" + "\n".join(rendered))
    return "\n\n".join(parts)[:PACKAGE_DOC_MAX]


def run(db_path: Path, config_path: Path | None = None) -> None:
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    from pydocs_mcp.application import (
        ModuleIntrospectionService,
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
    inspect_svc = ModuleIntrospectionService(package_store=package_store)

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def list_packages() -> str:
        """List indexed packages. '__project__' = your source code."""
        try:
            packages = await package_lookup.list_packages()
            sorted_pkgs = sorted(packages[:LIST_PACKAGES_MAX], key=lambda p: p.name)
            return "\n".join(
                f"- {p.name} {p.version} — {p.summary}" for p in sorted_pkgs
            )
        except Exception:
            log.warning("list_packages failed", exc_info=True)
            return "Error listing packages."

    @mcp.tool()
    async def get_package_doc(package: str) -> str:
        """Full docs for a package. Use '__project__' for your own code.

        Args:
            package: e.g. 'fastapi', 'vllm', '__project__'
        """
        try:
            pkg_name = _normalize_pkg_filter_value(package)
            doc = await package_lookup.get_package_doc(pkg_name)
            if doc is None:
                return f"'{package}' not found."
            return _render_package_doc(doc)
        except Exception:
            # Distinguish "storage raised" from "no matching row" so operators
            # reading tool output can tell an indexing gap apart from a bug.
            log.warning("get_package_doc failed", exc_info=True)
            return f"Error retrieving '{package}'."

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
        try:
            response = await search_docs_svc.search(
                _build_chunk_query(query, package, internal, topic),
            )
            return _render_search_response_chunks(response)
        except Exception:
            log.warning("search_docs failed", exc_info=True)
            return "No matches found."

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
        try:
            response = await search_api_svc.search(
                _build_member_query(query, package, internal),
            )
            return _render_search_response_members(response)
        except Exception:
            log.warning("search_api failed", exc_info=True)
            return "No symbols found."

    @mcp.tool()
    async def inspect_module(package: str, submodule: str = "") -> str:
        """Live-import a module to show its current API.

        Args:
            package: e.g. 'fastapi'
            submodule: e.g. 'routing' → fastapi.routing
        """
        try:
            return await inspect_svc.inspect(package, submodule)
        except Exception:
            log.warning("inspect_module failed", exc_info=True)
            return (
                f"'{package}' is not indexed. "
                "Use list_packages() to see available packages."
            )

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")
