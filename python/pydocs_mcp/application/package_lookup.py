"""PackageLookup — list + get_package_doc via UoW (spec §5.1, post-#5a-2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
)
from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class PackageLookup:
    """Composes the three domain stores (via UoW) into a read-only package view.

    Post-#5a-2: depends only on ``uow_factory``. Each public method opens a
    fresh UoW; reads run inside ``async with`` and exit without committing.

    Note: ``get_package_doc`` no longer uses ``asyncio.gather`` for the two
    list reads — both go through the same held connection inside the UoW,
    and concurrent access would race ``_sqlite_transaction``'s lock. Per
    spec §7.2, the two ~1ms SELECTs run sequentially.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def list_packages(self) -> tuple[Package, ...]:
        async with self.uow_factory() as uow:
            return tuple(await uow.packages.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(package_name)
            if pkg is None:
                return None
            chunks = await uow.chunks.list(
                filter={ChunkFilterField.PACKAGE.value: package_name},
                limit=10,
            )
            members = await uow.module_members.list(
                filter={ModuleMemberFilterField.PACKAGE.value: package_name},
                limit=30,
            )
        return PackageDoc(package=pkg, chunks=tuple(chunks), members=tuple(members))

    async def find_module(self, package: str, module: str) -> bool:
        if not package or not module:
            return False
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(
                filter={
                    ChunkFilterField.PACKAGE.value: package,
                    ChunkFilterField.MODULE.value: module,
                },
                limit=1,
            )
        return bool(chunks)
