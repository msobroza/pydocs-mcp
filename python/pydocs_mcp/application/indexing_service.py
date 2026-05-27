"""Application service coordinating write-side indexing (spec §5.6).

``IndexingService`` is a use-case service that owns the atomic
delete-then-upsert sequence across the five entity stores
(packages / chunks / module_members / trees / references). Sub-PR #5a-2
reduced the class to a single dependency: a ``uow_factory`` callable.
Each public method opens a UoW, drives the write sequence, and commits —
the "5 stores + optional UoW" shape is gone (eng-review bug #4: the old
reach-through wiring let the service operate without a transaction).

References flow into ``uow.references`` inside the same UoW as the rest
of the reindex sequence. The resolver runs as a post-pass within
``reindex_package``: it loads the cross-package qname universe from
``uow.trees`` (already inside the UoW), rewrites each candidate's
``to_node_id``, then writes via ``uow.references.save_many``.

Cross-package re-resolution: after writing the freshly-indexed
package's references, :meth:`ReferenceStore.resolve_unresolved` flips
``to_node_id`` for any previously-unresolved row whose ``to_name`` is
now an exact qname in the just-indexed package — catching the case
where package A's old ``to_name = "B.func"`` refs were unresolved at
the time A was indexed but B is now in the universe (spec C1
replaces the historical ``_held_conn`` reach-through with the new
Protocol method).

The service depends ONLY on Protocols — no SQLite, no concrete
repositories — so any backend (SQLite today, Postgres/DuckDB later)
can be plugged in as long as ``uow_factory()`` returns something that
structurally satisfies :class:`~pydocs_mcp.storage.protocols.UnitOfWork`.
``uow.vectors`` is always present (spec S15 — :class:`NullVectorStore`
covers the dense-disabled deployment), so this service never branches
on backend identity.
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
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger(__name__)


# S7: ``IndexingStats`` lives next to its sole producer
# (``ProjectIndexer.index_project``) — it accumulates write-side counters
# while the indexer iterates over packages, so it belongs in the
# application layer alongside the service that mutates it.
# ``pydocs_mcp.models.IndexingStats`` is a re-export shim that binds to
# this exact class object.
@dataclass(slots=True)
class IndexingStats:
    """Mutable accumulator for :meth:`ProjectIndexer.index_project`
    (spec §5.3). Deliberately NOT frozen — the service mutates these counters
    while iterating over packages. `slots=True` still guards against typos
    (e.g. ``stats.indexxed += 1``) by rejecting unknown attributes."""
    project_indexed: bool = False
    indexed: int = 0
    cached: int = 0
    failed: int = 0


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

        Canonical order: diff chunks by content_hash (delete removed +
        insert added, keep unchanged in place) → delete members → delete
        pkg → upsert pkg → trees (delete then save_many) → upsert members
        → delete references for package → write resolved references →
        cross-package re-resolution UPDATE → commit. The chunks-side
        diff-merge replaces the legacy ``delete + upsert`` pair so
        unchanged rows survive (and their vectors with them).

        ``references`` is emitted by :class:`ReferenceCaptureStage`;
        ``reference_aliases`` is its sibling alias map. ``class_attribute_types``
        is the per-class ``self.X`` → ``<type>`` table built by
        ``capture_self_attribute_types`` — feeds the resolver's Rule 0
        for cross-method ``self.X.Y`` inference. The resolver runs inside
        this method using the cross-package qname universe loaded from
        ``uow.trees`` (so it sees the just-upserted trees).

        Implementation: a thin orchestrator over :meth:`_diff_merge_chunks`
        (chunk diff + stale-vector cleanup) and :meth:`_persist_references`
        (sweep + resolve + save + cross-package re-resolution). Each helper
        is independently testable; the orchestrator reads as a sequence of
        named writes under one UoW (Task 7 I2).
        """
        # Enum-typed filter keys are the single source of truth the
        # safe-columns whitelist also derives from; the ``packages`` table
        # keys on ``name`` (no matching enum), so that one stays literal.
        async with self.uow_factory() as uow:
            removed_ids, added_chunks = await self._diff_merge_chunks(
                uow, package_name=package.name, incoming_chunks=chunks,
            )

            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)

            if added_chunks:
                await uow.chunks.insert(added_chunks)
                # AC-24 — forward only the newly-inserted chunks'
                # embeddings to the .tq sidecar. Unchanged chunks kept
                # their existing vectors (no re-add); removed chunks'
                # vectors were wiped above.
                await self._maybe_write_vectors(uow, package, added_chunks)
            # Tree persistence happens between chunks and members so
            # FK-like post-conditions line up if a future schema adds them.
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)

            await self._persist_references(
                uow,
                package_name=package.name,
                references=references,
                reference_aliases=reference_aliases or {},
                class_attribute_types=class_attribute_types or {},
            )

            await uow.commit()

    async def _diff_merge_chunks(
        self,
        uow: UnitOfWork,
        *,
        package_name: str,
        incoming_chunks: tuple[Chunk, ...],
    ) -> tuple[list[int], tuple[Chunk, ...]]:
        """Compute the chunk diff + apply stale-side cleanup (AC-3 + AC-8 + AC-9).

        Replaces the legacy ``chunks.delete + chunks.upsert`` pair. Diffs
        ``incoming_chunks`` against the persisted snapshot via
        ``content_hash``: keep unchanged rows + their vectors, drop only
        rows that disappeared (and their vectors), and return the
        genuinely new rows for the caller to insert. Pre-migration
        NULL-hash rows always count as 'removed' so they self-heal on
        the first reindex per package (spec AC-8).

        Returns ``(removed_ids, added_chunks)``. The caller is responsible
        for inserting ``added_chunks`` and forwarding their embeddings —
        keeping the insert here would couple the diff to the vector path
        and complicate the orchestrator's read flow.

        ``uow.vectors`` is always present (spec S15). SQLite-only
        deployments route through :class:`NullVectorStore`, whose
        ``remove_vectors`` is a silent no-op.
        """
        existing_pairs = await uow.chunks.list_id_hash_pairs(
            filter={ChunkFilterField.PACKAGE.value: package_name},
        )
        incoming_hashes = {c.content_hash for c in incoming_chunks}
        existing_by_hash = {h: cid for cid, h in existing_pairs if h}
        removed_ids = [
            cid for cid, h in existing_pairs
            if not h or h not in incoming_hashes
        ]
        added_chunks = tuple(
            c for c in incoming_chunks if c.content_hash not in existing_by_hash
        )

        if removed_ids:
            await uow.chunks.delete_by_ids(removed_ids)
            await uow.vectors.remove_vectors(removed_ids)

        return removed_ids, added_chunks

    async def _persist_references(
        self,
        uow: UnitOfWork,
        *,
        package_name: str,
        references: Sequence["NodeReference"],
        reference_aliases: dict[str, dict[str, str]],
        class_attribute_types: dict[str, dict[str, str]],
    ) -> None:
        """Atomic references rewrite for one package (sub-PR #5b + AC #6.5).

        Always sweeps this package's existing rows first, then writes the
        freshly-resolved ones (empty ``references`` = sweep only, leaves
        a clean row set for the next call). After writing, flips OTHER
        packages' previously-unresolved refs whose ``to_name`` is now an
        exact qname inside the just-indexed package's universe (AC #6.5).
        """
        await uow.references.delete_for_package(package_name)
        if references:
            resolved = await self._resolve_references(
                uow,
                references,
                reference_aliases,
                class_attribute_types,
            )
            await uow.references.save_many(
                resolved, package=package_name,
            )

        # AC #6.5 — cross-package re-resolution. After writing this
        # package's rows, flip OTHER packages' previously-unresolved
        # refs whose ``to_name`` is now an exact qname inside the
        # just-indexed package's universe.
        await self._reresolve_cross_package(uow, package_name)

    async def _maybe_write_vectors(
        self,
        uow: UnitOfWork,
        package: Package,
        input_chunks: tuple[Chunk, ...],
    ) -> None:
        """Forward chunk embeddings to ``uow.vectors``.

        Spec S15 — ``uow.vectors`` is always present. SQLite-only
        deployments route through :class:`NullVectorStore` (silent
        no-op on ``add_vectors``), composite SQLite + TurboQuant
        deployments route to the real backend.

        Diff-merge variant: ``input_chunks`` is the ``added_chunks``
        subset, NOT the full incoming batch. The persisted snapshot
        still includes the UNCHANGED rows the diff kept in place, so a
        positional align would map the wrong row to the wrong
        embedding. We match by ``content_hash`` instead — the hash is
        unique per (package, module, title, text, pipeline_hash) tuple,
        so it picks out exactly the freshly-inserted row for each input
        chunk.
        """
        input_hashes = {c.content_hash for c in input_chunks}
        if not input_hashes:
            return
        # Performance: if NONE of the input chunks carry an embedding,
        # there is nothing to forward — skip the persisted-row re-fetch
        # entirely. The SQLite-only deployment (ingestion pipelines
        # without ``EmbedChunksStage``) hits this branch on every reindex.
        if not any(c.embedding is not None for c in input_chunks):
            return
        # Re-fetch persisted rows by package — includes unchanged keepers
        # plus the just-inserted added chunks. We filter to only the rows
        # whose content_hash is in our input set (the added subset).
        persisted = await uow.chunks.list(
            filter={ChunkFilterField.PACKAGE.value: package.name},
        )
        persisted_for_input = [
            p for p in persisted if p.content_hash in input_hashes
        ]
        if len(persisted_for_input) != len(input_chunks):
            # Defensive: a hash-set mismatch means INSERT didn't land what
            # we asked for (or two inputs share a hash — should be
            # impossible given the hash inputs include title + text).
            # Silently skipping is safer than corrupting the .tq index.
            log.warning(
                "Skipping vector write for %s: persisted matching count %d "
                "does not match input chunk count %d.",
                package.name, len(persisted_for_input), len(input_chunks),
            )
            return
        by_hash = {p.content_hash: p for p in persisted_for_input}
        ids: list[int] = []
        embeddings: list[Embedding] = []
        for input_chunk in input_chunks:
            persisted_chunk = by_hash[input_chunk.content_hash]
            if input_chunk.embedding is None or persisted_chunk.id is None:
                continue
            ids.append(persisted_chunk.id)
            embeddings.append(input_chunk.embedding)
        if not ids:
            return
        await uow.vectors.add_vectors(ids, embeddings)

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
        """Re-resolve OTHER packages' refs against this package's qnames.

        Spec C1 — delegates to :meth:`ReferenceStore.resolve_unresolved`
        on the UoW. Replaces the historical backend-specific
        held-connection reach-through: the Protocol now owns the
        bulk-fixup contract and the filter adapter materialises the
        WHERE clause (so any backend — SQLite today, Postgres / DuckDB
        later — satisfies this code path without service-side changes;
        spec S33: this method does not name a concrete adapter).

        Scope: this only implements Rule B (exact qname match). Rules A
        (alias rewrite) / C (suffix) / D (ambiguous) / E (no match) are
        deferred — their cost/benefit on a typical self-index pass is
        marginal compared to the full resolver re-run that would be
        required.
        """
        # Build the qname universe for the just-indexed package only —
        # that's the set whose membership might newly resolve other
        # packages' unresolved refs.
        pkg_trees = await uow.trees.load_all_in_package(just_indexed_package)
        new_qnames: set[str] = set()
        for tree in pkg_trees.values():
            _add_qnames(tree, new_qnames)
        if new_qnames:
            await uow.references.resolve_unresolved(new_qnames)

    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / member / tree / ref it owns.

        Capture the soon-to-be-stale chunk IDs BEFORE deleting from
        SQLite, then wipe their vectors from the (real or null) backend
        after. Without this, a package's vectors outlive its SQLite
        rows on composite deployments and pollute future similarity
        searches with orphaned embeddings. Atomic via the surrounding
        UoW transaction. Spec S15 — :attr:`uow.vectors` is always
        present; SQLite-only deployments route through
        :class:`NullVectorStore` (silent no-op).
        """
        async with self.uow_factory() as uow:
            pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: name},
            )
            stale_vector_ids = [cid for cid, _ in pairs]
            await uow.chunks.delete(
                filter={ChunkFilterField.PACKAGE.value: name},
            )
            if stale_vector_ids:
                await uow.vectors.remove_vectors(stale_vector_ids)
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: name},
            )
            # Trees are per-package state too — without this delete a
            # stale tree survives a re-index and LookupService.get_tree
            # serves the pre-reindex payload.
            await uow.trees.delete_for_package(name)
            await uow.references.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        """Wipe every row across all five entity stores + every vector.

        Spec I3 — :meth:`UnitOfWork.delete_all` drives the per-store
        sweep in one call. Atomic within the UoW transaction. The
        ``vectors`` clear (NullVectorStore on SQLite-only deployments,
        TurboQuant on composite deployments) is part of that single
        delete sequence.
        """
        async with self.uow_factory() as uow:
            await uow.delete_all()
            await uow.commit()

    async def find_stale_packages(self, *, current_model: str) -> list[str]:
        """Return packages whose stored ``embedding_model`` differs from
        ``current_model``.

        Task 7 (I17): the legacy free function
        :func:`find_packages_with_stale_embeddings` is now a thin wrapper
        around this method, making the ``uow_factory`` dependency
        explicit on the service rather than implicit on a module-level
        callable.

        Used at startup (see ``__main__._run_indexing``) to detect when
        the YAML's ``embedding.model_name`` has changed since the last
        index pass. The composition root clears each returned package's
        ``content_hash`` so the next sweep re-extracts + re-embeds them
        via the existing hash-skip code path — no manual cache surgery.

        Packages with ``embedding_model is None`` are intentionally NOT
        flagged stale: they predate the embedding feature (no vectors in
        the .tq sidecar to mismatch) and will pick up a model tag on
        their next natural reindex. Flipping them here would trigger a
        blanket re-extract on every model rename for callers who haven't
        enabled embeddings yet.
        """
        async with self.uow_factory() as uow:
            all_pkgs = await uow.packages.list()
        return [
            p.name for p in all_pkgs
            if p.embedding_model is not None
            and p.embedding_model != current_model
        ]


def _add_qnames(node: "DocumentNode", out: set[str]) -> None:
    """Walk a DocumentNode tree, collect every qualified_name into ``out``."""
    out.add(node.qualified_name)
    for child in node.children:
        _add_qnames(child, out)


async def find_packages_with_stale_embeddings(
    *,
    uow_factory: Callable[[], UnitOfWork],
    current_model: str,
) -> list[str]:
    """Thin backwards-compat wrapper around :meth:`IndexingService.find_stale_packages`.

    Task 7 (I17): the canonical staleness check now lives as a method on
    :class:`IndexingService`, making the ``uow_factory`` dependency
    explicit on the service rather than implicit on this module-level
    callable. This wrapper exists so legacy callers that hold only a
    ``uow_factory`` (and not yet an :class:`IndexingService`) continue to
    work without churning callsites — but new code should use
    ``IndexingService(uow_factory=...).find_stale_packages(...)``.
    """
    return await IndexingService(uow_factory=uow_factory).find_stale_packages(
        current_model=current_model,
    )
