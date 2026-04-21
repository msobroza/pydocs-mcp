"""MCP server exposing search tools over indexed docs.

Handlers are thin adapters over application-layer services (spec §5.1,
AC #7): ``PackageLookupService``, ``SearchDocsService``, ``SearchApiService``,
``ModuleIntrospectionService``, ``DocumentTreeService``. All rendering +
filter-dict construction is kept in this module so the services stay
transport-agnostic.

Byte-parity with pre-PR tool I/O is a hard requirement (AC #8) — the
``_render_*`` helpers below are the single source of truth for handler
output shape.

Sub-PR #5 adds two new tree-exposure handlers (spec §13.1, §13.2, §16 AC #2):
``get_document_tree(package, module)`` and ``get_package_tree(package)``.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from pydocs_mcp.extraction.document_node import DocumentNode

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


def _node_to_dict(node: "DocumentNode") -> dict[str, Any]:
    """Convert a :class:`DocumentNode` to a plain dict tree for JSON.

    Kept handler-local (not promoted to ``extraction/``) so the MCP
    transport layer owns its own serialization shape — the storage
    layer's private ``_serialize_tree_to_json`` happens to share this
    schema today, but the two use-sites are free to diverge without
    forcing a cross-module refactor.
    """
    return {
        "node_id":        node.node_id,
        "qualified_name": node.qualified_name,
        "title":          node.title,
        "kind":           node.kind.value,
        "source_path":    node.source_path,
        "start_line":     node.start_line,
        "end_line":       node.end_line,
        "text":           node.text,
        "content_hash":   node.content_hash,
        "summary":        node.summary,
        "extra_metadata": dict(node.extra_metadata),
        "parent_id":      node.parent_id,
        "children":       [_node_to_dict(c) for c in node.children],
    }


def _serialize_tree_to_json(node: "DocumentNode") -> str:
    """Render a ``DocumentNode`` tree as pretty JSON for MCP output.

    Uses 2-space indentation so clients rendering the string in a
    terminal get a human-readable view; the storage layer's compact
    serialization is intentionally NOT reused here (different audience).
    """
    return json.dumps(_node_to_dict(node), indent=2)


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
        DocumentTreeService,
        ModuleIntrospectionService,
        NotFoundError,
        PackageLookupService,
        SearchApiService,
        SearchDocsService,
    )
    from pydocs_mcp.extraction import build_package_tree
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.storage.sqlite import (
        SqliteChunkRepository,
        SqliteDocumentTreeStore,
        SqlitePackageRepository,
    )

    config = AppConfig.load(explicit_path=config_path)
    context = build_retrieval_context(db_path, config)
    provider = context.connection_provider
    package_store = SqlitePackageRepository(provider=provider)
    chunk_store = SqliteChunkRepository(provider=provider)
    member_store = context.module_member_store
    tree_store = SqliteDocumentTreeStore(provider=provider)
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
    document_tree_svc = DocumentTreeService(tree_store=tree_store)

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

    @mcp.tool()
    async def get_document_tree(package: str, module: str) -> str:
        """Return the DocumentNode tree for (package, module) as JSON (spec §13.1).

        Args:
            package: Package name (e.g. 'requests', '__project__').
            module: Dotted module path within the package.

        Returns:
            Indented JSON of the module's DocumentNode tree, or a user-
            readable error message if the tree is missing or retrieval fails.
        """
        try:
            tree = await document_tree_svc.get_tree(package, module)
            return _serialize_tree_to_json(tree)
        except NotFoundError:
            return f"No tree for '{package}/{module}'."
        except Exception:  # noqa: BLE001 -- MCP boundary: return user-readable error
            log.warning("get_document_tree failed", exc_info=True)
            return f"Error retrieving tree for '{package}/{module}'."

    @mcp.tool()
    async def get_package_tree(package: str) -> str:
        """Return the package arborescence as JSON (spec §13.2).

        Walks every MODULE :class:`DocumentNode` stored for ``package`` and
        folds them into a PACKAGE root via :func:`build_package_tree` — dotted
        module names become a path trie with synthetic SUBPACKAGE nodes at
        each intermediate level.

        Args:
            package: Package name (e.g. 'requests', '__project__').

        Returns:
            Indented JSON of the PACKAGE root with SUBPACKAGE/MODULE
            children, or a user-readable message if no trees are indexed
            for this package.
        """
        try:
            modules = await document_tree_svc.list_package_modules(package)
            if not modules:
                return f"No indexed trees for package '{package}'."
            root = build_package_tree(package, modules)
            return _serialize_tree_to_json(root)
        except Exception:  # noqa: BLE001 -- MCP boundary: return user-readable error
            log.warning("get_package_tree failed", exc_info=True)
            return f"Error retrieving package tree for '{package}'."

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")
