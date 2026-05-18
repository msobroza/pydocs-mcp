"""Application service coordinating write-side indexing (spec §5.6).

``IndexingService`` is a use-case service that owns the atomic
delete-then-upsert sequence across the five entity stores
(packages / chunks / module_members / trees / references). Sub-PR #5a-2
reduced the class to a single dependency: a ``uow_factory`` callable.
Each public method opens a UoW, drives the write sequence, and commits —
the "5 stores + optional UoW" shape is gone (eng-review bug #4: the old
reach-through wiring let the service operate without a transaction).

Sub-PR #5b: references flow into ``uow.references`` inside the same UoW
as the rest of the reindex sequence. The resolver runs as a post-pass
within ``reindex_package``: it loads the cross-package qname universe
from ``uow.trees`` (already inside the UoW), rewrites each candidate's
``to_node_id``, then writes via ``uow.references.save_many``.

Cross-package re-resolution (AC #6.5): after writing the freshly-indexed
package's references, a targeted UPDATE re-runs Rule B (exact match)
resolution on any unresolved refs whose ``to_name`` is now in the
just-indexed package's qname universe — catching the case where package
A's old ``to_name = "B.func"`` refs were unresolved at the time A was
indexed but B is now in the universe. The UPDATE reaches into the held
SQLite connection via ``_held_conn``; FakeUoW returns ``None`` for that
attribute and the call is a silent no-op (fakes don't exercise
cross-package re-resolution).

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
from typing import TYPE_CHECKING

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
    from pydocs_mcp.storage.node_reference import NodeReference

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
        references: Sequence["NodeReference"] = (),
        reference_aliases: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Replace every row for ``package.name`` atomically (spec §13.3).

        Canonical order: delete chunks → delete members → delete pkg →
        upsert pkg → upsert chunks → trees (delete then save_many) →
        upsert members → delete references for package → write resolved
        references → cross-package re-resolution UPDATE → commit.

        ``references`` is emitted by :class:`ReferenceCaptureStage`;
        ``reference_aliases`` is its sibling alias map. The resolver
        runs inside this method using the cross-package qname universe
        loaded from ``uow.trees`` (so it sees the just-upserted trees).
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

            # Sub-PR #5b — references. Always sweep this package's
            # existing reference rows first, then write the freshly-
            # resolved ones (empty ``references`` = sweep only, leaves
            # a clean row set for the next call).
            await uow.references.delete_for_package(package.name)
            if references:
                resolved = await self._resolve_references(
                    uow, references, reference_aliases or {},
                )
                await uow.references.save_many(
                    resolved, package=package.name,
                )

            # AC #6.5 — cross-package re-resolution. After writing this
            # package's rows, flip OTHER packages' previously-unresolved
            # refs whose ``to_name`` is now an exact qname inside the
            # just-indexed package's universe.
            await self._reresolve_cross_package(uow, package.name)

            await uow.commit()

    async def _resolve_references(
        self,
        uow: UnitOfWork,
        refs: Sequence["NodeReference"],
        aliases: dict[str, dict[str, str]],
    ) -> list["NodeReference"]:
        """Build the cross-package qname universe + run the resolver."""
        from pydocs_mcp.extraction.strategies.reference_resolver import (
            ReferenceResolver,
        )

        # Universe = every indexed qname across every package. We load
        # trees per-package via uow.trees.load_all_in_package — one call
        # per package. For #5b this is acceptable; a future PR can add a
        # ``qnames_only`` fast path on DocumentTreeStore.
        universe: set[str] = set()
        all_pkgs = await uow.packages.list(limit=10_000)
        for pkg in all_pkgs:
            pkg_trees = await uow.trees.load_all_in_package(pkg.name)
            for tree in pkg_trees.values():
                _add_qnames(tree, universe)

        resolver = ReferenceResolver(
            qname_universe=frozenset(universe), aliases=aliases,
        )
        return resolver.resolve(refs)

    async def _reresolve_cross_package(
        self, uow: UnitOfWork, just_indexed_package: str,
    ) -> None:
        """AC #6.5 — re-resolve OTHER packages' refs against this package's qnames.

        Controller decision A1 (plan §): we punt on a ``bulk_resolve``
        Protocol method. Instead, reach into the held SQLite connection
        via ``_held_conn`` for a raw UPDATE. FakeUoW returns ``None`` for
        ``_held_conn`` and the call is a silent no-op — fakes don't
        exercise cross-package re-resolution.

        Scope: this only implements Rule B (exact qname match). Rules A
        (alias rewrite) / C (suffix) / D (ambiguous) / E (no match) are
        deferred — their cost/benefit on a typical self-index pass is
        marginal compared to the full resolver re-run that would be
        required.
        """
        import sqlite3
        conn = getattr(uow, "_held_conn", None)
        if conn is None or not isinstance(conn, sqlite3.Connection):
            return
        # Build the qname universe for the just-indexed package only —
        # that's the set whose membership might newly resolve other
        # packages' unresolved refs.
        pkg_trees = await uow.trees.load_all_in_package(just_indexed_package)
        new_qnames: set[str] = set()
        for tree in pkg_trees.values():
            _add_qnames(tree, new_qnames)
        # Rule B fast path: UPDATE unresolved rows whose to_name exactly
        # equals a new qname. The ix_refs_to_name index makes each row
        # lookup O(log n); for a 100k-row table the loop runs in <100ms.
        import asyncio
        for qname in new_qnames:
            await asyncio.to_thread(
                conn.execute,
                "UPDATE node_references SET to_node_id = ? "
                "WHERE to_node_id IS NULL AND to_name = ?",
                (qname, qname),
            )

    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / member / tree / ref it owns."""
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
            # Sub-PR #5b: reference rows are per-package state too.
            await uow.references.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        """Wipe every row across all five entity stores.

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
            # Sub-PR #5b: references store mirrors the trees store —
            # dedicated ``delete_all`` for the unconditional sweep.
            await uow.references.delete_all()
            await uow.packages.delete(filter=match_all)
            await uow.commit()


def _add_qnames(node: "DocumentNode", out: set[str]) -> None:
    """Walk a DocumentNode tree, collect every qualified_name into ``out``."""
    out.add(node.qualified_name)
    for child in node.children:
        _add_qnames(child, out)
