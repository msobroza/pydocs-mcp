"""TreeService — query-side wrapper over DocumentTreeStore (spec §13.1).

Post-#5a-2: depends only on a ``uow_factory: Callable[[], UnitOfWork]``.
Each method opens its own UoW, reads through ``uow.trees``, and exits
without committing (read-only).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode


@dataclass(frozen=True, slots=True)
class TreeService:
    """Fetches DocumentNode trees through a per-call UnitOfWork.

    ``branch`` is the branch every read answers from (spec §6.4, #313),
    forwarded unchanged to the tree store: ``None`` — the default — reads the
    served default branch, a name that branch; both plus the dependency tier.
    """

    uow_factory: Callable[[], UnitOfWork]
    branch: str | None = None

    def on_branch(self, branch: str | None) -> TreeService:
        """This service reading ``branch``'s trees; ``None`` keeps it as it is."""
        return self if branch is None else replace(self, branch=branch)

    async def get_tree(
        self,
        package: str,
        module: str,
    ) -> DocumentNode | None:
        async with self.uow_factory() as uow:
            return await uow.trees.load(package, module, branch=self.branch)

    async def exists(self, package: str, module: str) -> bool:
        async with self.uow_factory() as uow:
            return await uow.trees.exists(package, module, branch=self.branch)

    async def list_package_modules(
        self,
        package: str,
    ) -> dict[str, DocumentNode]:
        async with self.uow_factory() as uow:
            return await uow.trees.load_all_in_package(package, branch=self.branch)
