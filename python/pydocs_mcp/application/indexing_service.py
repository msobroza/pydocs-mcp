"""Application service coordinating write-side indexing (spec §5.6).

`IndexingService` is a use-case service that owns the atomic
delete-then-upsert sequence across the three entity stores
(``PackageStore`` / ``ChunkStore`` / ``ModuleMemberStore``). It depends
ONLY on Protocols — no SQLite, no concrete repositories — so any
backend (SQLite today, Postgres/DuckDB later) can be plugged in as
long as it implements the storage Protocols (AC #10).

When a ``UnitOfWork`` is supplied, all three mutations run inside a
single ``begin()`` scope so partial indexing state never becomes
visible; when it is ``None`` the calls execute directly (used for
non-transactional backends or in tests with Protocol fakes).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
)
from pydocs_mcp.storage.filters import All
from pydocs_mcp.storage.protocols import (
    ChunkStore,
    ModuleMemberStore,
    PackageStore,
    UnitOfWork,
)

T = TypeVar("T")

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing across 3 entity stores.

    Depends ONLY on Protocols — backend-agnostic (spec §5.6, AC #10).
    """

    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore
    unit_of_work: UnitOfWork | None = None

    def __post_init__(self) -> None:
        # Warn when the service is constructed without a UoW — ``_do_reindex``
        # runs delete-then-upsert, and without a transaction boundary a
        # mid-sequence crash can leave the package row wiped but chunks /
        # members only partially re-inserted. Safe for tests with Protocol
        # fakes (no persistence, no partial-visibility risk), but dangerous
        # against a real backend. Dataclass frozen=True still permits
        # __post_init__ — it just blocks ``self.x = y`` on declared fields.
        if self.unit_of_work is None:
            log.warning(
                "IndexingService constructed without UnitOfWork — writes are "
                "NOT atomic; partial reindex state can become visible on failure.",
            )

    async def _in_uow(
        self, coro_fn: Callable[..., Awaitable[T]], /, *args, **kwargs,
    ) -> T:
        """Run ``coro_fn`` inside a ``unit_of_work.begin()`` scope if configured.

        Collapses the three identical ``if self.unit_of_work is not None: async with
        self.unit_of_work.begin(): ... else: ...`` shims that used to wrap
        ``_do_reindex`` / ``_do_remove`` / ``_do_clear_all`` into a single helper.
        """
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                return await coro_fn(*args, **kwargs)
        return await coro_fn(*args, **kwargs)

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
    ) -> None:
        """Replace every row for ``package.name`` atomically.

        Deletes the package row and every chunk / module-member tagged
        with the same package name, then upserts the new fixture. When
        a ``UnitOfWork`` is configured the whole sequence runs inside
        one transaction.
        """
        await self._in_uow(self._do_reindex, package, chunks, module_members)

    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / module-member it owns."""
        await self._in_uow(self._do_remove, name)

    async def clear_all(self) -> None:
        """Wipe every row across all three entity stores.

        Uses ``All(clauses=())`` — an empty conjunction that the
        ``SqliteFilterAdapter`` translates to ``1 = 1``. That form matches
        NULL columns too, unlike the previous ``LIKE '%'`` hack, and keeps
        the delete semantics unconditional without adding a new
        ``delete_all()`` method to the store Protocols.
        """
        await self._in_uow(self._do_clear_all)

    async def _do_reindex(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
    ) -> None:
        # Enum-typed filter keys — stringly-typed ``{"package": ...}`` drifted
        # silently when a column renamed; ``ChunkFilterField`` / ``ModuleMemberFilterField``
        # are the single source of truth the safe-columns whitelist also derives from.
        # The ``packages`` table keys on ``name`` (not ``package``), so that
        # one stays literal — no matching enum today (noted in /simplify report).
        await self.chunk_store.delete(filter={ChunkFilterField.PACKAGE.value: package.name})
        await self.module_member_store.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: package.name},
        )
        await self.package_store.delete(filter={"name": package.name})
        await self.package_store.upsert(package)
        await self.chunk_store.upsert(chunks)
        await self.module_member_store.upsert_many(module_members)

    async def _do_remove(self, name: str) -> None:
        await self.chunk_store.delete(filter={ChunkFilterField.PACKAGE.value: name})
        await self.module_member_store.delete(
            filter={ModuleMemberFilterField.PACKAGE.value: name},
        )
        await self.package_store.delete(filter={"name": name})

    async def _do_clear_all(self) -> None:
        match_all: All = All(clauses=())
        await self.chunk_store.delete(filter=match_all)
        await self.module_member_store.delete(filter=match_all)
        await self.package_store.delete(filter=match_all)
