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
        """
        # Enum-typed filter keys are the single source of truth the
        # safe-columns whitelist also derives from; the ``packages`` table
        # keys on ``name`` (no matching enum), so that one stays literal.
        async with self.uow_factory() as uow:
            # === BEGIN diff-merge for chunks (AC-3 + AC-8 + AC-9) ===
            # Replaces the legacy ``chunks.delete + chunks.upsert`` pair.
            # We diff incoming chunks against the persisted snapshot by
            # ``content_hash``: keep unchanged rows + their vectors, drop
            # only the rows that disappeared, insert only the genuinely
            # new chunks. Pre-migration NULL-hash rows always count as
            # 'removed' so they self-heal on the first reindex per
            # package (spec AC-8).
            existing_pairs = await uow.chunks.list_id_hash_pairs(
                filter={ChunkFilterField.PACKAGE.value: package.name},
            )
            incoming_hashes = {c.content_hash for c in chunks}
            existing_by_hash = {h: cid for cid, h in existing_pairs if h}
            removed_ids = [
                cid for cid, h in existing_pairs
                if not h or h not in incoming_hashes
            ]
            added_chunks = tuple(
                c for c in chunks if c.content_hash not in existing_by_hash
            )

            if removed_ids:
                await uow.chunks.delete_by_ids(removed_ids)
                # AC-9 — only the composite UoW exposes ``vectors``; the
                # SQLite-only path is a silent no-op.
                vectors_store = getattr(uow, "vectors", None)
                if vectors_store is not None:
                    await vectors_store.remove_vectors(removed_ids)

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
            # === END diff-merge ===
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

        Diff-merge variant (AC-3): ``input_chunks`` is now the
        ``added_chunks`` subset, NOT the full incoming batch. The
        persisted snapshot still includes the UNCHANGED rows the diff
        kept in place, so a positional align would map the wrong row to
        the wrong embedding. We match by ``content_hash`` instead — the
        hash is unique per (package, module, title, text, pipeline_hash)
        tuple, so it picks out exactly the freshly-inserted row for each
        input chunk.
        """
        vectors_store = getattr(uow, "vectors", None)
        if vectors_store is None:
            return
        input_hashes = {c.content_hash for c in input_chunks}
        if not input_hashes:
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
        """Delete a package and every chunk / member / tree / ref it owns.

        AC-4: capture the soon-to-be-stale chunk IDs BEFORE deleting from
        SQLite, then wipe their vectors from the TurboQuant sidecar after.
        Without this, a package's vectors outlive its SQLite rows and
        pollute future similarity searches with orphaned embeddings.
        Atomic via the surrounding UoW transaction; the
        ``getattr(uow, 'vectors', None)`` gate keeps the SQLite-only path
        unchanged (AC-9).
        """
        async with self.uow_factory() as uow:
            stale_vector_ids: list[int] = []
            if getattr(uow, "vectors", None) is not None:
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
            # serves the pre-reindex payload (F5b from /ultrareview).
            await uow.trees.delete_for_package(name)
            # Sub-PR #5b: reference rows are per-package state too.
            await uow.references.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        """Wipe every row across all five entity stores + every vector.

        Uses ``All(clauses=())`` — an empty conjunction the
        ``SqliteFilterAdapter`` translates to ``1 = 1``. That form
        matches NULL columns too, unlike the previous ``LIKE '%'`` hack,
        and keeps the delete semantics unconditional without adding a
        new ``delete_all()`` method to the entity-store Protocols.

        AC-5: when the composite UoW exposes ``vectors``, also wipe the
        in-memory ``IdMapIndex`` so the next commit serializes an empty
        ``.tq`` sidecar. The SQLite-only UoW lacks ``.vectors`` — the
        ``getattr`` gate keeps that path unchanged (matches the AC-9
        idiom used in ``reindex_package`` and ``remove_package``).
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
            if getattr(uow, "vectors", None) is not None:
                await uow.vectors.clear_all()
            await uow.commit()


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
    """Return packages whose stored ``embedding_model`` differs from ``current_model``.

    Used at startup (see ``__main__._run_indexing``) to detect when the
    YAML's ``embedding.model_name`` has changed since the last index
    pass. The composition root clears each returned package's
    ``content_hash`` so the next sweep re-extracts + re-embeds them via
    the existing hash-skip code path — no manual cache surgery.

    Packages with ``embedding_model is None`` are intentionally NOT
    flagged stale: they predate the embedding feature (no vectors in
    the .tq sidecar to mismatch) and will pick up a model tag on their
    next natural reindex. Flipping them here would trigger a blanket
    re-extract on every model rename for callers who haven't enabled
    embeddings yet.
    """
    async with uow_factory() as uow:
        all_pkgs = await uow.packages.list()
    return [
        p.name for p in all_pkgs
        if p.embedding_model is not None
        and p.embedding_model != current_model
    ]
