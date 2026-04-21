"""DocumentTreeService — query-side wrapper over DocumentTreeStore (spec §13.1).

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
class DocumentTreeService:
    """Fetches DocumentNode trees from a DocumentTreeStore.

    frozen+slots for immutable value semantics + typo guard — matches the
    rest of the application-layer service pattern established in sub-PR #4.
    """

    tree_store: DocumentTreeStore

    async def get_tree(self, package: str, module: str) -> "DocumentNode":
        """Return the tree for ``(package, module)`` or raise NotFoundError.

        The store's ``load`` returns None on miss; we translate to a
        typed exception so MCP handlers can map to a clean error message.
        """
        tree = await self.tree_store.load(package, module)
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
