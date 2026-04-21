"""PackageLookupService — list + get_package_doc via stores (spec §5.1)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
)
from pydocs_mcp.storage.protocols import (
    ChunkStore,
    ModuleMemberStore,
    PackageStore,
)


@dataclass(frozen=True, slots=True)
class PackageLookupService:
    """Composes the three domain stores into a read-only package view.

    ``list_packages`` returns up to 200 packages — enough to populate an MCP
    catalogue tool in a single call. ``get_package_doc`` gathers a compact
    "at a glance" bundle (first 10 chunks, first 30 members) for UI surfaces
    that preview a package without running a full retrieval pipeline.
    """

    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore

    async def list_packages(self) -> tuple[Package, ...]:
        return tuple(await self.package_store.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        pkg = await self.package_store.get(package_name)
        if pkg is None:
            # Short-circuit so unknown names don't waste two extra store
            # round-trips producing empty lists.
            return None
        # Performance: the two list() calls target different tables and are
        # fully independent, so issuing them concurrently halves the observed
        # latency of a get_package_doc() call under async-capable backends.
        chunks, members = await asyncio.gather(
            self.chunk_store.list(
                filter={ChunkFilterField.PACKAGE.value: package_name},
                limit=10,
            ),
            self.module_member_store.list(
                filter={ModuleMemberFilterField.PACKAGE.value: package_name},
                limit=30,
            ),
        )
        return PackageDoc(package=pkg, chunks=tuple(chunks), members=tuple(members))

    async def find_module(self, package: str, module: str) -> bool:
        """Return True iff at least one indexed Chunk exists for (package, module).

        Added by sub-PR #6 — used by LookupService._longest_indexed_module to
        resolve dotted-path targets when tree_svc (from #5) is unavailable.
        Empty arguments short-circuit to False without querying the store.
        """
        if not package or not module:
            return False
        chunks = await self.chunk_store.list(
            filter={
                ChunkFilterField.PACKAGE.value: package,
                ChunkFilterField.MODULE.value: module,
            },
            limit=1,
        )
        return bool(chunks)
