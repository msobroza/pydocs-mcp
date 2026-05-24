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
    Embedding,
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
        class_attribute_types: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """Replace every row for ``package.name`` atomically (spec §13.3).

        Canonical order: delete chunks → delete members → delete pkg →
        upsert pkg → upsert chunks → trees (delete then save_many) →
        upsert members → delete references for package → write resolved
        references → cross-package re-resolution UPDATE → commit.

        ``references`` is emitted by :class:`ReferenceCaptureStage`;
        ``reference_aliases`` is its sibling alias map. ``class_attribute_types``
        is the per-class ``self.X`` → ``<type>`` table built by
        ``capture_self_attribute_types`` — feeds the resolver's Rule 0
        for cross-method ``self.X.Y`` inference. The resolver runs inside
        this method using the cross-package qname universe loaded from
        ``uow.trees`` (so it sees the just-upserted trees).
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
            # AC-24: when the UoW is composite (SqliteUnitOfWork +
            # TurboQuantUnitOfWork), forward every chunk's embedding to
            # the .tq sidecar under the auto-assigned ``INTEGER PRIMARY
            # KEY`` id SQLite just gave each row (rowid-alias id, not
            # AUTOINCREMENT). The SqliteUnitOfWork-only path is a no-op
            # (``getattr`` returns None) so legacy callers stay green.
            await self._maybe_write_vectors(uow, package, chunks)
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
                    uow,
                    references,
                    reference_aliases or {},
                    class_attribute_types or {},
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

    async def _maybe_write_vectors(
        self,
        uow: UnitOfWork,
        package: Package,
        input_chunks: tuple[Chunk, ...],
    ) -> None:
        """AC-24: forward chunk embeddings to ``uow.vectors`` when present.

        Composite UoW (SQLite + TurboQuant) exposes ``vectors``; the
        SQLite-only UoW does not — ``getattr`` returns ``None`` and the
        method becomes a no-op so the legacy single-backend path stays
        identical.

        The SQLite ``id`` column is a rowid-alias ``INTEGER PRIMARY
        KEY`` (not AUTOINCREMENT): ``chunks.upsert`` did not return the
        assigned IDs (``executemany`` doesn't), so we re-fetch by package
        and sort by id. Because ``delete`` cleared the package's rows
        first and ``executemany`` inserts in input order, the id-sorted
        persisted list is positionally aligned with ``input_chunks`` —
        index ``i`` in both lists is the same logical chunk. We then keep
        only the pairs where the input chunk carried an embedding; mixed
        batches (some embedded, some not) skip the bare entries instead
        of failing the whole write.
        """
        vectors_store = getattr(uow, "vectors", None)
        if vectors_store is None:
            return
        # Re-fetch the just-upserted rows to learn their assigned IDs.
        # ``delete`` ran before ``upsert`` so this returns exactly the
        # rows ``executemany`` just inserted — no stale survivors.
        persisted = await uow.chunks.list(
            filter={ChunkFilterField.PACKAGE.value: package.name},
        )
        if len(persisted) != len(input_chunks):
            # Defensive: a row count mismatch means our positional pairing
            # would associate the wrong embedding to the wrong id. Silently
            # skipping is safer than corrupting the .tq index.
            log.warning(
                "Skipping vector write for %s: persisted row count %d "
                "does not match input chunk count %d.",
                package.name, len(persisted), len(input_chunks),
            )
            return
        persisted_sorted = sorted(persisted, key=lambda c: c.id or 0)
        ids: list[int] = []
        embeddings: list[Embedding] = []
        for persisted_chunk, input_chunk in zip(
            persisted_sorted, input_chunks, strict=True,
        ):
            if input_chunk.embedding is None or persisted_chunk.id is None:
                continue
            ids.append(persisted_chunk.id)
            embeddings.append(input_chunk.embedding)
        if not ids:
            return
        await vectors_store.add_vectors(ids, embeddings)

    async def _resolve_references(
        self,
        uow: UnitOfWork,
        refs: Sequence["NodeReference"],
        aliases: dict[str, dict[str, str]],
        class_attribute_types: dict[str, dict[str, str]],
    ) -> list["NodeReference"]:
        """Build the cross-package qname universe + run the resolver.

        Sub-PR follow-up to #5c (AC #15 stdlib-idx): when
        ``reference_graph.resolver.include_stdlib`` is True (default), merges
        the bundled stdlib + builtins qnames into the universe so CALLS edges
        like ``os.path.join`` / ``len`` / ``asyncio.to_thread`` resolve instead
        of staying ``to_node_id=None``.
        """
        from pydocs_mcp.extraction.strategies.reference_resolver import (
            ReferenceResolver,
        )
        from pydocs_mcp.extraction.strategies.stdlib_qnames import (
            _get_resolver_config,
            load_stdlib_qnames,
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

        # AC #15 stdlib-idx: merge bundled stdlib qnames if enabled in YAML.
        # The toggle is read at call time so YAML reloads / test overrides
        # take effect on the next reindex without re-importing.
        cfg = _get_resolver_config()
        if cfg.include_stdlib:
            universe.update(load_stdlib_qnames())

        resolver = ReferenceResolver(
            qname_universe=frozenset(universe),
            aliases=aliases,
            class_attribute_types=class_attribute_types,
            strict_suffix=cfg.strict_suffix,
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
