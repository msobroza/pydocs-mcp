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
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import (
    decision_key,
    reconcile,
    staleness_score,
)
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    Embedding,
    ModuleMember,
    ModuleMemberFilterField,
    MultiVector,
    Package,
    is_multi_vector,
)
from pydocs_mcp.storage.decision_record import DecisionRecord
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
    # Opt-in (reference_graph.node_scores.enabled). When True,
    # recompute_node_scores() runs a single post-index pass computing PageRank /
    # community / in-degree into the node_scores table. Needs the [graph] extra.
    node_scores_enabled: bool = False

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: Sequence[DocumentNode] = (),
        references: Sequence[NodeReference] = (),
        reference_aliases: dict[str, dict[str, str]] | None = None,
        class_attribute_types: dict[str, dict[str, str]] | None = None,
        decisions: Sequence[RawDecision] = (),
        decision_structured: Mapping[str, tuple[dict[str, object], str]] | None = None,
        project_root: Path | None = None,
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

        ``decisions`` is the merged :class:`RawDecision` tuple emitted by
        the ``capture_decisions`` sub-pipeline (project targets only; dependency
        packages pass ``()``). They are reconciled + persisted BEFORE the
        chunk diff so each decision chunk's ``decision_id`` metadata can be
        stamped from the ``decision_key`` → id map before it lands
        (spec §D8-§D10). ``project_root`` is where the staleness scorer
        ``os.stat``\\s the affected files; when absent, staleness is left at 0.

        Implementation: a thin orchestrator over :meth:`_persist_decisions`
        (reconcile + upsert + delete + backlink map), :meth:`_diff_merge_chunks`
        (chunk diff + stale-vector cleanup) and :meth:`_persist_references`
        (sweep + resolve + save + cross-package re-resolution). Each helper
        is independently testable; the orchestrator reads as a sequence of
        named writes under one UoW (Task 7 I2).
        """
        _require_matching_package(package.name, chunks)
        # Enum-typed filter keys are the single source of truth the
        # safe-columns whitelist also derives from; the ``packages`` table
        # keys on ``name`` (no matching enum), so that one stays literal.
        async with self.uow_factory() as uow:
            # Decisions first: reconcile + persist so each decision chunk gets
            # its record id stamped into metadata BEFORE the chunk diff inserts
            # it. ``decision_id`` is not a content_hash input (the hash keys on
            # package/module/title/text only), so stamping never re-triggers a
            # re-embed — it just backlinks the searchable projection to its row.
            key_to_id = await self._persist_decisions(
                uow,
                package_name=package.name,
                decisions=decisions,
                decision_structured=decision_structured or {},
                project_root=project_root,
            )
            chunks = _stamp_decision_ids(chunks, key_to_id)

            # ``removed_ids`` is computed for the parallel vector-removal branch
            # in ``reindex_package``; the upsert path here doesn't need it
            # because the followup ``packages.delete`` + cascading FK clears the
            # chunk rows en masse.
            _removed_ids, added_chunks = await self._diff_merge_chunks(
                uow,
                package_name=package.name,
                incoming_chunks=chunks,
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

        MULTISET, not set, semantics: a package can legitimately carry
        several chunks whose identity tuple (package/module/title/text)
        — and therefore ``content_hash`` — is identical (#69). A plain
        hash-SET diff is blind to multiplicity: dropping one of two
        duplicate-hash rows would leave both persisted (the surviving
        hash is still "in" the incoming set), and adding a second
        duplicate-hash chunk would be silently excluded (the hash is
        already "in" the existing set). Per-hash COUNTS fix both
        directions: for each hash, keep min(existing, incoming) rows,
        delete the existing excess, and insert the incoming excess.

        Kept (hash-matched) rows additionally get their v15 span columns
        refreshed from the freshly-extracted metadata via
        :meth:`ChunkStore.refresh_span_metadata` — spans are outside the
        content hash, so without this a pre-v15 row (or one written before
        a chunker emitted spans) would never acquire them.

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
        # Group persisted ids by hash (NULL/empty hash never matches any
        # incoming chunk, so its rows always end up in the removed side).
        existing_ids_by_hash: dict[str, list[int]] = defaultdict(list)
        for cid, h in existing_pairs:
            if h:
                existing_ids_by_hash[h].append(cid)

        incoming_by_hash: dict[str, list[Chunk]] = defaultdict(list)
        for c in incoming_chunks:
            incoming_by_hash[c.content_hash].append(c)

        removed_ids: list[int] = [cid for cid, h in existing_pairs if not h]
        added_chunks: list[Chunk] = []
        kept_chunks: list[Chunk] = []
        for h, existing_ids in existing_ids_by_hash.items():
            incoming_for_hash = incoming_by_hash.get(h, [])
            keep_count = min(len(existing_ids), len(incoming_for_hash))
            # Keep the first `keep_count` existing rows; the rest are stale
            # excess (multiplicity shrank) and get removed.
            removed_ids.extend(existing_ids[keep_count:])
            # Any incoming chunks beyond `keep_count` are genuinely new
            # (multiplicity grew) and get added.
            added_chunks.extend(incoming_for_hash[keep_count:])
            kept_chunks.extend(incoming_for_hash[:keep_count])
        # Hashes with no existing rows at all are entirely new.
        for h, incoming_for_hash in incoming_by_hash.items():
            if h not in existing_ids_by_hash:
                added_chunks.extend(incoming_for_hash)

        if removed_ids:
            await uow.chunks.delete_by_ids(removed_ids)
            await uow.vectors.remove_vectors(removed_ids)

        if kept_chunks:
            # v15 span backfill: spans are deliberately OUTSIDE content_hash,
            # so a hash-matched kept row written pre-v15 (or before a chunker
            # change) would otherwise never acquire source_path / start_line /
            # end_line. Cheap unconditional UPDATE per kept row — touches only
            # the three span columns (no embedded flag, no FTS, no re-embed).
            await uow.chunks.refresh_span_metadata(package_name, tuple(kept_chunks))

        return removed_ids, tuple(added_chunks)

    async def _persist_decisions(
        self,
        uow: UnitOfWork,
        *,
        package_name: str,
        decisions: Sequence[RawDecision],
        decision_structured: Mapping[str, tuple[dict[str, object], str]],
        project_root: Path | None,
    ) -> dict[str, int]:
        """Reconcile + persist mined decisions, return the ``decision_key`` → id map.

        Loads the persisted rows for this package, reconciles the merged
        incoming decisions against them (preserving ids / created_at /
        supersession, §D9), scores each upsert's staleness against
        ``project_root`` mtimes (§D10), overlays the §D12 LLM-structured fields
        + verification tier where present, writes the upserts, deletes the
        vanished rows, and returns a ``decision_key`` → assigned-id map so the
        caller can backlink each decision chunk's ``decision_id`` metadata.

        Empty ``decisions`` still runs: reconcile against ``()`` deletes every
        persisted row for the package (all sources vanished) and returns an
        empty map. Dependency packages therefore pass ``decisions=()`` and this
        cleans up any decisions a prior project-mode index left behind.
        """
        existing = await uow.decisions.list_for_package(package_name)
        merged = tuple(decisions)
        result = reconcile(
            existing=existing, incoming=merged, now=time.time(), package=package_name
        )
        scored = tuple(
            self._apply_structured(
                replace(
                    record,
                    staleness_score=self._score_staleness(record, project_root),
                ),
                decision_structured,
            )
            for record in result.upserts
        )
        assigned_ids = await uow.decisions.upsert(scored)
        if result.delete_ids:
            await uow.decisions.delete_by_ids(result.delete_ids)
        return {
            decision_key(record.title): decision_id
            for record, decision_id in zip(scored, assigned_ids, strict=True)
        }

    @staticmethod
    def _score_staleness(record: DecisionRecord, project_root: Path | None) -> float:
        """Staleness for one record (0.0 when ``project_root`` is unknown)."""
        if project_root is None:
            return 0.0
        return staleness_score(
            affected_files=record.affected_files,
            updated_at=record.updated_at,
            now=time.time(),
            root=project_root,
        )

    @staticmethod
    def _apply_structured(
        record: DecisionRecord,
        overlay: Mapping[str, tuple[dict[str, object], str]],
    ) -> DecisionRecord:
        """Overlay the §D12 grounded structured fields + verification tier.

        ``overlay`` maps ``decision_key(title) -> (grounded fields, tier)``. A
        record whose key isn't in the overlay passes through untouched — keeping
        the engine's ``structured=None`` / ``verification="verbatim"`` default
        (the deterministic-mining path, which never touches an LLM).
        """
        entry = overlay.get(decision_key(record.title))
        if entry is None:
            return record
        fields, verification = entry
        return replace(record, structured=fields, verification=verification)

    async def _persist_references(
        self,
        uow: UnitOfWork,
        *,
        package_name: str,
        references: Sequence[NodeReference],
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
                resolved,
                package=package_name,
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
        naive positional align would map the wrong row to the wrong
        embedding, and a naive hash-only match would collapse multiple
        same-hash rows onto one. ``_build_persisted_queue_by_hash`` does
        a MULTISET match by ``content_hash`` — see its docstring — so
        each input chunk pairs with its own freshly-inserted row, even
        when several chunks share a hash (#69).
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
        persisted_for_input = [p for p in persisted if p.content_hash in input_hashes]
        persisted_queue_by_hash = _build_persisted_queue_by_hash(
            persisted_for_input,
            input_chunks,
            package_name=package.name,
        )
        if persisted_queue_by_hash is None:
            return
        # Late-interaction split: dispatch each chunk's embedding to the
        # store that matches its shape (single-vector .tq vs multi-vector
        # PLAID). Extracted to ``_route_embeddings_by_shape`` to keep this
        # method under the complexity gate.
        sv_ids, sv_embeddings, mv_ids, mv_embeddings = _route_embeddings_by_shape(
            input_chunks, persisted_queue_by_hash
        )
        if sv_ids:
            await uow.vectors.add_vectors(sv_ids, sv_embeddings)
            # Stamp chunks.embedded in the SAME transaction as the vector
            # write so the flag mirrors the .tq exactly. The integrity check
            # compares vectors against this flag — chunks a selective embed
            # policy skips stay 0 and never read as drift.
            await uow.chunks.mark_embedded(sv_ids)
        if mv_ids:
            await uow.multi_vectors.add_vectors(mv_ids, mv_embeddings)

    async def _resolve_references(
        self,
        uow: UnitOfWork,
        refs: Sequence[NodeReference],
        aliases: dict[str, dict[str, str]],
        class_attribute_types: dict[str, dict[str, str]],
    ) -> list[NodeReference]:
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
        # ``project_qnames`` is the ``__project__`` subset — Rule C's scope
        # for project code, whose prefixless qnames the from_package prefix
        # filter can never match (ADR 0004 fix iii).
        universe: set[str] = set()
        project_qnames: set[str] = set()
        all_pkgs = await uow.packages.list(limit=10_000)
        for pkg in all_pkgs:
            pkg_trees = await uow.trees.load_all_in_package(pkg.name)
            for tree in pkg_trees.values():
                _add_qnames(tree, universe)
                if pkg.name == PROJECT_PACKAGE_NAME:
                    _add_qnames(tree, project_qnames)

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
            project_qnames=frozenset(project_qnames),
        )
        return resolver.resolve(refs)

    async def _reresolve_cross_package(
        self,
        uow: UnitOfWork,
        just_indexed_package: str,
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
            await uow.node_scores.delete_for_package(name)
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

    async def recompute_node_scores(self) -> None:
        """Recompute the ``node_scores`` table over the FULL reference graph.

        A single post-index pass — global PageRank / Louvain communities must
        see the fully-resolved cross-package graph, so this runs ONCE after
        :meth:`ProjectIndexer.index_project` finishes (and its cross-package
        re-resolution), NOT per package. No-op unless ``node_scores_enabled``;
        degrades gracefully (logs a warning) when the ``[graph]`` extra is
        absent, leaving the table empty so the rerank steps simply no-op.
        """
        if not self.node_scores_enabled:
            return
        # Deferred import: the helper guards the optional networkx dependency.
        from pydocs_mcp.application.node_score_compute import compute_scores

        async with self.uow_factory() as uow:
            edges = await uow.references.resolved_edges()
            chunks = await uow.chunks.list()
            qname_packages = {
                qn: pkg
                for c in chunks
                if (qn := c.metadata.get("qualified_name")) and (pkg := c.metadata.get("package"))
            }
            try:
                scores = compute_scores(edges, qname_packages)
            except ImportError as exc:
                log.warning("node_scores recompute skipped — %s", exc)
                return
            except Exception as exc:
                # The node-score pass is an optional, advisory post-index step;
                # a graph-algorithm failure (e.g. a networkx convergence error)
                # must never fail the whole index after chunks are committed.
                log.warning("node_scores recompute failed — %s", exc)
                return
            await uow.node_scores.delete_all()
            await uow.node_scores.upsert(scores)
            await uow.commit()
        log.info("node_scores: recomputed %d nodes", len(scores))

    @staticmethod
    def _stale_packages(
        packages: Sequence[Package],
        current_model: str,
    ) -> list[Package]:
        """Packages whose recorded embedder differs from ``current_model``.

        ``embedding_model is None`` rows are intentionally NOT stale: they
        predate the embedding feature (no vectors in the .tq sidecar to
        mismatch) and pick up a model tag on their next natural reindex.
        Flipping them here would trigger a blanket re-extract on every
        model rename for callers who haven't enabled embeddings yet.
        """
        return [
            p
            for p in packages
            if p.embedding_model is not None and p.embedding_model != current_model
        ]

    async def find_stale_packages(self, *, current_model: str) -> list[str]:
        """Return packages whose stored ``embedding_model`` differs from
        ``current_model``.

        Read-only companion to :meth:`invalidate_stale_embeddings`, which
        additionally clears each stale package's ``content_hash`` in the
        same transaction. See :meth:`_stale_packages` for why
        ``embedding_model is None`` rows are skipped.
        """
        async with self.uow_factory() as uow:
            all_pkgs = await uow.packages.list()
        return [p.name for p in self._stale_packages(all_pkgs, current_model)]

    async def invalidate_stale_embeddings(self, *, current_model: str) -> list[str]:
        """Clear ``content_hash`` on every package embedded with another model.

        One UoW = one transaction: the stale-set read and the clearing
        upserts land atomically — no read/write gap where a concurrent
        index pass could observe half-cleared state. An empty
        ``content_hash`` never equals a freshly-extracted package's real
        hash, so the skip check in ``ProjectIndexer``
        (``existing.content_hash == pkg.content_hash``) falls through to a
        full re-extract + re-embed under the current model.

        Returns the stale package names (for the caller's log line).
        """
        async with self.uow_factory() as uow:
            all_pkgs = await uow.packages.list()
            stale = self._stale_packages(all_pkgs, current_model)
            if not stale:
                return []
            for pkg in stale:
                await uow.packages.upsert(replace(pkg, content_hash=""))
            await uow.commit()
        return [p.name for p in stale]


def _require_matching_package(package_name: str, chunks: tuple[Chunk, ...]) -> None:
    """Reject chunks whose ``metadata["package"]`` is missing or diverges from
    ``package_name`` BEFORE any write happens.

    ``_chunk_to_row`` (storage/sqlite/row_mappers.py) defaults a missing
    ``package`` key to ``""``, and every package-scoped re-fetch in this
    service (``_diff_merge_chunks``, ``_maybe_write_vectors``) filters
    strictly on ``package_name``. A chunk persisted under the wrong package
    value is therefore invisible to those re-fetches: it silently trips the
    ``_maybe_write_vectors`` length-mismatch guard (skipping vector writes
    for the WHOLE batch, not just the bad chunk) and — because it never
    matches ``package_name`` on a later diff — is re-inserted as "new" on
    every subsequent reindex, an orphan row ``remove_package(name)`` can
    never see. Failing loudly here, with the offending chunk's metadata,
    surfaces the extractor bug immediately instead of degrading dense
    search for every well-formed sibling chunk.
    """
    for chunk in chunks:
        chunk_package = chunk.metadata.get(ChunkFilterField.PACKAGE.value)
        if chunk_package != package_name:
            raise ValueError(
                f"chunk package metadata {chunk_package!r} does not match "
                f"package.name {package_name!r}; expected metadata[{ChunkFilterField.PACKAGE.value!r}] "
                f"== {package_name!r} (chunk title={chunk.metadata.get(ChunkFilterField.TITLE.value)!r})"
            )


def _build_persisted_queue_by_hash(
    persisted_for_input: list[Chunk],
    input_chunks: tuple[Chunk, ...],
    *,
    package_name: str,
) -> dict[str, list[Chunk]] | None:
    """Pair each ``input_chunks`` entry with its OWN freshly-inserted persisted row.

    MULTISET match, not a single-valued dict or a bare length check:
    ``input_chunks`` can legitimately contain >1 chunk sharing a hash (#69
    content-identical chunks), each persisted as its OWN row via
    ``insert()``. ``persisted_for_input`` can ALSO legitimately exceed
    ``len(input_chunks)`` per hash — e.g. one same-hash row was kept by the
    diff (unchanged) while a second same-hash row was freshly added, so the
    re-fetch returns both but ``input_chunks`` only covers the added one.
    ``uow.chunks.list`` returns rows in ascending (insertion) rowid order,
    and ``insert()`` assigns strictly increasing autoincrement ids — so for
    any hash, the freshly-added rows are always the numerically-LAST rows
    for that hash. This groups persisted rows per hash (ascending id
    order) and keeps only the tail slice of size ``count`` (the
    just-inserted rows), reversed so the caller's ``.pop()`` yields them
    front-to-back — pairing the Nth added input chunk for a hash with the
    Nth freshly-inserted row for that hash, never a row that predates this
    reindex.

    Returns ``None`` if any hash has fewer persisted rows than expected
    (INSERT didn't land what was asked for) — the caller must skip the
    vector write entirely rather than risk corrupting the ``.tq`` index.
    """
    persisted_by_hash: dict[str, list[Chunk]] = defaultdict(list)
    for p in persisted_for_input:
        persisted_by_hash[p.content_hash].append(p)

    queue_by_hash: dict[str, list[Chunk]] = {}
    for content_hash, count in Counter(c.content_hash for c in input_chunks).items():
        rows_for_hash = persisted_by_hash.get(content_hash, [])
        if len(rows_for_hash) < count:
            log.warning(
                "Skipping vector write for %s: persisted matching count %d "
                "for hash is less than input chunk count %d.",
                package_name,
                len(rows_for_hash),
                count,
            )
            return None
        tail = rows_for_hash[-count:] if count else []
        tail.reverse()
        queue_by_hash[content_hash] = tail
    return queue_by_hash


def _route_embeddings_by_shape(
    input_chunks: tuple[Chunk, ...],
    persisted_queue_by_hash: dict[str, list[Chunk]],
) -> tuple[list[int], list[Embedding], list[int], list[MultiVector]]:
    """Pair each input chunk with its own freshly-inserted persisted row and
    split by embedding shape. Extracted from ``_maybe_write_vectors`` to keep
    that method under the cognitive-complexity gate.

    Multiset pairing: a package can carry >1 chunk with identical
    ``(package, module, title, text)`` -> identical ``content_hash``. Each
    persisted row is popped exactly once per matching input chunk, so
    genuinely distinct rows each get their own vector write — no id is emitted
    twice (which would trip ``IdMapIndex.add_with_ids``: "id N already
    present") and no row is silently starved of its embedding. ``np.ndarray``
    embeddings route to the single-vector store, ``list[np.ndarray]`` to the
    multi-vector store — distinct row shapes, so mis-routing would corrupt the
    backing index.
    """
    sv_ids: list[int] = []
    sv_embeddings: list[Embedding] = []
    mv_ids: list[int] = []
    mv_embeddings: list[MultiVector] = []
    seen_ids: set[int] = set()
    for input_chunk in input_chunks:
        queue = persisted_queue_by_hash[input_chunk.content_hash]
        if not queue:
            continue
        persisted_chunk = queue.pop()
        if input_chunk.embedding is None or persisted_chunk.id is None:
            continue
        if persisted_chunk.id in seen_ids:
            continue
        seen_ids.add(persisted_chunk.id)
        if is_multi_vector(input_chunk.embedding):
            mv_ids.append(persisted_chunk.id)
            mv_embeddings.append(input_chunk.embedding)
        else:
            sv_ids.append(persisted_chunk.id)
            sv_embeddings.append(input_chunk.embedding)
    return sv_ids, sv_embeddings, mv_ids, mv_embeddings


def _stamp_decision_ids(chunks: tuple[Chunk, ...], key_to_id: dict[str, int]) -> tuple[Chunk, ...]:
    """Backlink decision chunks to their persisted rows via ``decision_id``.

    A decision chunk carries a ``decision_key`` in its metadata; this rewrites
    that chunk's metadata with the matching ``decision_id`` from ``key_to_id``.
    Non-decision chunks (and decision chunks whose key didn't resolve) pass
    through untouched. ``decision_id`` is NOT a ``content_hash`` input, so the
    rewrite never disturbs the chunk diff.
    """
    if not key_to_id:
        return chunks
    stamped: list[Chunk] = []
    for chunk in chunks:
        key = chunk.metadata.get("decision_key")
        decision_id = key_to_id.get(key) if isinstance(key, str) else None
        if decision_id is None:
            stamped.append(chunk)
            continue
        new_metadata = {**dict(chunk.metadata), "decision_id": decision_id}
        stamped.append(replace(chunk, metadata=new_metadata))
    return tuple(stamped)


def _add_qnames(node: DocumentNode, out: set[str]) -> None:
    """Walk a DocumentNode tree, collect every qualified_name into ``out``."""
    out.add(node.qualified_name)
    for child in node.children:
        _add_qnames(child, out)
