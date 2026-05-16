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
    from pydocs_mcp.extraction.model import DocumentNode


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

        Mirrors :class:`DocumentTreeStore.load`'s contract; callers that
        want a typed exception can wrap themselves. The None-on-miss form
        is what ``LookupService._longest_indexed_module`` iterates over
        while probing dotted-prefix candidates.
        """
        return await self.tree_store.load(package, module)

    async def exists(self, package: str, module: str) -> bool:
        """Return whether a tree row exists for ``(package, module)``.

        Cheap probe — no JSON parse, no ``DocumentNode`` allocation. Used
        by ``LookupService._longest_indexed_module`` so the dotted-prefix
        walk doesn't deserialize candidates it'll discard; the winning
        candidate still goes through ``get_tree`` once downstream.
        """
        return await self.tree_store.exists(package, module)

    async def list_package_modules(
        self, package: str,
    ) -> dict[str, "DocumentNode"]:
        """Return dict module → DocumentNode for every module in a package.

        Empty dict if the package has no indexed trees — caller decides
        how to surface that (e.g. ``get_package_tree`` may raise; ``tree``
        CLI may print "no trees").
        """
        return await self.tree_store.load_all_in_package(package)
