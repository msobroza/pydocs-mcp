"""TreeService — query-side wrapper over DocumentTreeStore (spec §13.1).

Used by the ``get_document_tree`` MCP handler (Task 25) and the
``pydocs-mcp tree`` CLI (Task 28) to fetch a previously-stored tree by
``(package, module)``. Depends only on the :class:`DocumentTreeStore`
Protocol — keeps the application layer backend-agnostic (AC #10).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.storage.protocols import DocumentTreeStore

if TYPE_CHECKING:
    from pydocs_mcp.extraction.document_node import DocumentNode


class NotFoundError(LookupError):
    """Raised when no tree exists for the requested (package, module) key.

    Subclass of LookupError so callers that don't care about the exact
    type can still except-match idiomatically.
    """


@dataclass(frozen=True, slots=True)
class TreeService:
    """Fetches DocumentNode trees from a DocumentTreeStore.

    frozen+slots for immutable value semantics + typo guard — matches the
    rest of the application-layer service pattern established in sub-PR #4.
    """

    tree_store: DocumentTreeStore

    async def get_tree(
        self, package: str, module: str,
    ) -> "DocumentNode | None":
        """Return the tree for ``(package, module)`` or ``None`` on miss.

        Mirrors :class:`DocumentTreeStore.load`'s contract. Callers that
        need a typed exception use :meth:`get_tree_or_raise`; the
        None-on-miss form is what ``LookupService._longest_indexed_module``
        iterates over while probing dotted-prefix candidates.
        """
        return await self.tree_store.load(package, module)

    async def get_tree_or_raise(
        self, package: str, module: str,
    ) -> "DocumentNode":
        """Same as :meth:`get_tree` but raises ``NotFoundError`` on miss.

        Use when the caller has no fallback path and wants a clean MCP
        error message instead of branching on ``None``.
        """
        tree = await self.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no document tree for {package}/{module}")
        return tree

    async def list_package_modules(
        self, package: str,
    ) -> dict[str, "DocumentNode"]:
        """Return dict module → DocumentNode for every module in a package.

        Empty dict if the package has no indexed trees — caller decides
        whether to treat that as NotFoundError (e.g. ``get_package_tree``
        wants to raise on empty; ``tree`` CLI may want to print "no trees").
        """
        return await self.tree_store.load_all_in_package(package)
