"""Application service coordinating write-side indexing (spec §5.6).

``IndexingService`` is a use-case service that owns the atomic
delete-then-upsert sequence across the four entity stores
(packages / chunks / module_members / trees). Sub-PR #5a-2 reduces the
class to a single dependency: a ``uow_factory`` callable. Each public
method opens a UoW, drives the write sequence, and commits — the
"5 stores + optional UoW" shape is gone (eng-review bug #4: the old
reach-through wiring let the service operate without a transaction).

The service depends ONLY on Protocols — no SQLite, no concrete
repositories — so any backend (SQLite today, Postgres/DuckDB later)
can be plugged in as long as ``uow_factory()`` returns something that
structurally satisfies :class:`~pydocs_mcp.storage.protocols.UnitOfWork`
(AC #10).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
)
from pydocs_mcp.storage.filters import All
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing through a UnitOfWork (spec §5.6).

    Single dependency — ``uow_factory: Callable[[], UnitOfWork]``. The
    service opens a UoW per public-method call, drives the write
    sequence inside it, and commits. All writes are atomic; partial
    indexing state never becomes visible (eng-review §14 bug #4).
    """

    uow_factory: Callable[[], UnitOfWork]

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: Sequence["DocumentNode"] = (),
        references: Sequence[Any] = (),  # noqa: ARG002 -- sub-PR #5b seam
    ) -> None:
        """Replace every row for ``package.name`` atomically (spec §13.3).

        Canonical order: package → chunks → trees → members. ``trees``
        only triggers tree-store calls when non-empty (no point deleting
        nothing). ``references`` is accepted as a seam for sub-PR #5b
        (cross-node reference graph) but ignored today.
        """
        # Enum-typed filter keys are the single source of truth the
        # safe-columns whitelist also derives from; the ``packages`` table
        # keys on ``name`` (no matching enum), so that one stays literal.
        async with self.uow_factory() as uow:
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: package.name},
            )
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            # Tree persistence happens between chunks and members so
            # FK-like post-conditions line up if a future schema adds them.
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)
            await uow.commit()

    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / member / tree it owns."""
        async with self.uow_factory() as uow:
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: name},
            )
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: name},
            )
            # Trees are per-package state too — without this delete a
            # stale tree survives a re-index and LookupService.get_tree
            # serves the pre-reindex payload (F5b from /ultrareview).
            await uow.trees.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        """Wipe every row across all four entity stores.

        Uses ``All(clauses=())`` — an empty conjunction the
        ``SqliteFilterAdapter`` translates to ``1 = 1``. That form
        matches NULL columns too, unlike the previous ``LIKE '%'`` hack,
        and keeps the delete semantics unconditional without adding a
        new ``delete_all()`` method to the entity-store Protocols.
        """
        match_all: All = All(clauses=())
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter=match_all)
            await uow.module_members.delete(filter=match_all)
            # Trees store has a dedicated ``delete_all`` — match the
            # destructive sweep across all entity stores; without this,
            # document_trees rows accumulate indefinitely across
            # clear_all cycles.
            await uow.trees.delete_all()
            await uow.packages.delete(filter=match_all)
            await uow.commit()
