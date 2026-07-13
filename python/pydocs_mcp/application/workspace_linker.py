"""Workspace-level cross-repo link pass (spec 2026-07-11 §3.3, §A1.3, §A1.8).

Resolves each bundle's persisted UNRESOLVED references against the qname
universes of its *sibling* bundles and writes the resulting cross-edges to
the overlay ``CrossLinkStore``. Bundles are read-only throughout (G6/N6).

NOT a single-``uow_factory`` service: it spans MULTIPLE bundles (one
read-only uow factory per bundle) plus the overlay store — there is no
multi-bundle UnitOfWork and the overlay is its own transactional world
(spec §3.3; the documented exception-by-necessity, like the retrieval
pipelines' ConnectionProvider carve-out).

Local-first contract (spec §A1.8, AC32): the linker's input is exclusively
``to_node_id IS NULL`` rows — a reference the bundle-local resolver already
resolved (e.g. the sibling's package was installed at index time) is never
scanned, never linked, never duplicated in the overlay.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydocs_mcp.application.similar_linker import NullSimilarLinkGenerator
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.cross_link_edge import (
    CrossLinkEdge,
    LinkedBundleStamp,
    WorkspaceNodeScore,
)
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import CrossLinkStore, UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.application.protocols import SimilarGenerator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BundleHandle:
    """One loaded bundle as the linker sees it (identity + read-only access)."""

    project: str
    bundle_stem: str
    bundle_path: str
    indexed_at: float
    git_head: str | None
    uow_factory: Callable[[], UnitOfWork]
    # Stamped embedder identity (index_metadata) — the SIMILAR gate's inputs
    # (spec §A1.2). Empty defaults deliberately FAIL the strict fingerprint
    # comparison, so a bundle without stamps never generates similar edges.
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0
    pipeline_hash: str = ""


@dataclass(frozen=True, slots=True)
class LinkReport:
    """What one link pass did — printed by the ``link`` verb, logged on serve.

    Counter fields cover the full A1.4 set; the SIMILAR / workspace-score
    fields are populated by their landing slices and default inert here.
    """

    unresolved_scanned: Mapping[str, int]
    edges_created: Mapping[str, int]
    collisions: Mapping[str, int]
    alias_resolved: int = 0
    alias_ambiguous: int = 0
    similar_edges: int = 0
    embedder_mismatches: int = 0
    workspace_scores_computed: bool = False
    pagerank_available: bool = False
    per_pair_similar_seconds: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _UniverseEntry:
    """One exported qname: which package exports it and at what precedence."""

    package: str
    is_project_source: bool


@dataclass(frozen=True, slots=True)
class _Counters:
    """Mutable tallies for one link pass (dataclass fields hold the dicts)."""

    unresolved_scanned: dict[str, int] = field(default_factory=dict)
    edges_created: dict[str, int] = field(default_factory=dict)
    collisions: dict[str, int] = field(default_factory=dict)
    alias: dict[str, int] = field(default_factory=lambda: {"resolved": 0, "ambiguous": 0})


@dataclass(frozen=True, slots=True)
class WorkspaceLinker:
    """Resolves persisted unresolved references across sibling bundles."""

    bundles: tuple[BundleHandle, ...]
    cross_links: CrossLinkStore
    kinds: tuple[ReferenceKind, ...]
    match_scope: Literal["project_only", "all_packages"]
    alias_resolution: Literal["imports_graph", "off"]
    # A1.1: recompute workspace scores after any edge-set change. in_degree
    # is always computed; pagerank only when the [graph] extra is present.
    workspace_scores: bool = True
    # A1.2: query-driven SIMILAR generation — the Null default keeps the
    # pass inert unless the composition root wired a real generator
    # (``similar`` in cross_repo.kinds AND an embedder available).
    similar_generator: SimilarGenerator = field(default_factory=NullSimilarLinkGenerator)

    async def link(self, stale_projects: frozenset[str] | None = None) -> LinkReport:
        """Run the pass; write edges/stamps only for ``stale_projects``.

        ``None`` means a full pass (every project stale). Matching is always
        computed in memory over all sources — the incremental unit is the
        WRITE: ``replace_edges_touching(P)`` swaps only P-touching edges
        (spec §3.8), and edges between two non-stale projects stay untouched
        in the overlay.
        """
        stale = (
            frozenset(b.project for b in self.bundles) if stale_projects is None else stale_projects
        )
        universes: dict[str, dict[str, _UniverseEntry]] = {}
        imports_resolved: dict[str, dict[str, list[str]]] = {}
        imports_unresolved: dict[str, dict[str, list[str]]] = {}
        unresolved: dict[str, list[NodeReference]] = {}
        for bundle in self.bundles:
            (
                universes[bundle.project],
                imports_resolved[bundle.project],
                imports_unresolved[bundle.project],
                unresolved[bundle.project],
            ) = await self._read_bundle(bundle)

        counters = _Counters()
        touching: dict[str, set[CrossLinkEdge]] = {b.project: set() for b in self.bundles}
        for source in self.bundles:
            refs = unresolved[source.project]
            counters.unresolved_scanned[source.project] = len(refs)
            for ref in refs:
                edge = self._match(
                    source, ref, universes, imports_resolved, imports_unresolved, counters
                )
                if edge is not None:
                    touching[edge.from_project].add(edge)
                    touching[edge.to_project].add(edge)

        similar_total, mismatches, per_pair = await self._generate_similar(stale, touching)
        linked_at = time.time()
        wrote_any = False
        for bundle in sorted(self.bundles, key=lambda b: b.project):
            if bundle.project not in stale:
                continue
            wrote_any = True
            edges = tuple(sorted(touching[bundle.project], key=_edge_sort_key))
            await self.cross_links.replace_edges_touching(bundle.project, edges)
            await self.cross_links.stamp_bundle(
                LinkedBundleStamp(
                    bundle_stem=bundle.bundle_stem,
                    project_name=bundle.project,
                    bundle_path=bundle.bundle_path,
                    indexed_at=bundle.indexed_at,
                    git_head=bundle.git_head,
                    linked_at=linked_at,
                )
            )
        wrote_any |= await self._purge_departed()
        scores_computed, pagerank_available = await self._recompute_scores(
            universes, changed=wrote_any
        )
        return LinkReport(
            unresolved_scanned=dict(counters.unresolved_scanned),
            edges_created=dict(counters.edges_created),
            collisions=dict(counters.collisions),
            alias_resolved=counters.alias["resolved"],
            alias_ambiguous=counters.alias["ambiguous"],
            similar_edges=similar_total,
            embedder_mismatches=mismatches,
            workspace_scores_computed=scores_computed,
            pagerank_available=pagerank_available,
            per_pair_similar_seconds=per_pair,
        )

    async def _generate_similar(
        self,
        stale: frozenset[str],
        touching: dict[str, set[CrossLinkEdge]],
    ) -> tuple[int, int, dict[str, float]]:
        """§A1.2 SIMILAR pairs, scoped per §3.8 step (iii): only ordered pairs
        touching a stale project regenerate — edges between two non-stale
        projects survive untouched in the overlay (their batches aren't
        written), so skipping them here is both correct and the cost bound.
        """
        if ReferenceKind.SIMILAR not in self.kinds:
            return 0, 0, {}
        total, mismatches = 0, 0
        per_pair: dict[str, float] = {}
        for source, target in self._stale_pairs(stale):
            outcome = await self.similar_generator.generate_pair(source, target)
            if not outcome.active:
                return 0, 0, {}
            mismatches += 1 if outcome.embedder_mismatch else 0
            per_pair[f"{source.project}->{target.project}"] = outcome.seconds
            total += len(outcome.edges)
            for edge in outcome.edges:
                touching[edge.from_project].add(edge)
                touching[edge.to_project].add(edge)
        return total, mismatches, per_pair

    def _stale_pairs(self, stale: frozenset[str]) -> list[tuple[BundleHandle, BundleHandle]]:
        """Ordered bundle pairs with at least one stale endpoint (§3.8 iii)."""
        return [
            (source, target)
            for source in self.bundles
            for target in self.bundles
            if source.project != target.project
            and (source.project in stale or target.project in stale)
        ]

    async def _purge_departed(self) -> bool:
        """Drop edges + stamps of bundles no longer in the workspace (AC19)."""
        loaded = {b.project for b in self.bundles}
        purged = False
        for stamp in await self.cross_links.bundle_stamps():
            if stamp.project_name in loaded:
                continue
            await self.cross_links.replace_edges_touching(stamp.project_name, ())
            await self.cross_links.delete_stamp(stamp.bundle_stem)
            purged = True
        return purged

    async def _recompute_scores(
        self,
        universes: dict[str, dict[str, _UniverseEntry]],
        *,
        changed: bool,
    ) -> tuple[bool, bool]:
        """A1.1 workspace scores over the union graph (composite node ids).

        ``in_degree`` is pure counting (always available); ``pagerank`` rides
        the existing [graph]-extra computation — absent extra degrades to
        NULL pagerank + a single warning, never a raise. Global recompute on
        ANY edge-set change; ``workspace_scores: false`` drops the table.
        """
        if not self.workspace_scores:
            await self.cross_links.replace_workspace_scores(())
            return False, False
        if not changed:
            return False, False
        edges = await self._union_edges()
        nodes = {
            f"{project}:{qname}" for project, universe in universes.items() for qname in universe
        }
        in_degree: dict[str, int] = {}
        for _src, dst in edges:
            in_degree[dst] = in_degree.get(dst, 0) + 1
        pagerank, pagerank_available = _try_pagerank(edges)
        rows = tuple(
            WorkspaceNodeScore(
                project=node.split(":", 1)[0],
                qualified_name=node.split(":", 1)[1],
                pagerank=pagerank.get(node) if pagerank_available else None,
                in_degree=in_degree.get(node, 0),
            )
            for node in sorted(nodes | set(in_degree))
        )
        await self.cross_links.replace_workspace_scores(rows)
        return True, pagerank_available

    async def _union_edges(self) -> list[tuple[str, str]]:
        """Composite-qualified union: bundle-local resolved edges ∪ ALL persisted
        cross edges.

        Cross edges come from ``cross_links.all_edges()`` — the persisted
        overlay — NOT the in-memory pass set: an incremental relink only
        regenerates stale-touching edges, so scoring off the pass set would
        drop the SIMILAR (and any) edges between two non-stale siblings that
        survive untouched in the overlay, and the recomputed scores would
        diverge from the persisted graph until the next full relink (§A1.1).
        """
        edges: list[tuple[str, str]] = []
        for bundle in self.bundles:
            async with bundle.uow_factory() as uow:
                pairs = await uow.references.list_resolved(self.kinds)
            edges.extend(
                (f"{bundle.project}:{src}", f"{bundle.project}:{dst}") for src, dst in pairs
            )
        edges.extend(
            (
                f"{e.from_project}:{e.from_node_id}",
                f"{e.to_project}:{e.to_node_id}",
            )
            for e in await self.cross_links.all_edges()
        )
        return edges

    async def _read_bundle(
        self, bundle: BundleHandle
    ) -> tuple[
        dict[str, _UniverseEntry],
        dict[str, list[str]],
        dict[str, list[str]],
        list[NodeReference],
    ]:
        """One read-only pass over a bundle: universe, imports maps, unresolved.

        Reads go through the bundle's UoW (no commit; ``__aexit__`` rollback
        is a no-op — the CLAUDE.md atomicity model for read paths).
        """
        async with bundle.uow_factory() as uow:
            universe = await self._universe_of(uow)
            resolved_imports: dict[str, list[str]] = {}
            for from_id, to_id in await uow.references.list_resolved((ReferenceKind.IMPORTS,)):
                resolved_imports.setdefault(from_id, []).append(to_id)
            unresolved_imports: dict[str, list[str]] = {}
            unresolved_rows = await uow.references.list_unresolved(self.kinds)
            for row in await uow.references.list_unresolved((ReferenceKind.IMPORTS,)):
                unresolved_imports.setdefault(row.from_node_id, []).append(row.to_name)
        return universe, resolved_imports, unresolved_imports, unresolved_rows

    async def _universe_of(self, uow: UnitOfWork) -> dict[str, _UniverseEntry]:
        """Exported qnames per ``match_scope`` from the bundle's trees."""
        universe: dict[str, _UniverseEntry] = {}
        for package in await uow.packages.list():
            is_root = package.name == PROJECT_PACKAGE_NAME
            if self.match_scope == "project_only" and not is_root:
                continue
            trees = await uow.trees.load_all_in_package(package.name)
            qnames: set[str] = set()
            for tree in trees.values():
                _collect_qnames(tree, qnames)
            for qname in qnames:
                # Under match_scope=all_packages a bundle can export the same
                # qname from BOTH __project__ and a dependency copy; a
                # project-source entry must never be shadowed by a later
                # dependency one (package-list order is arbitrary), or the
                # collision precedence in _match would flip (is_project_source).
                existing = universe.get(qname)
                if existing is not None and existing.is_project_source and not is_root:
                    continue
                universe[qname] = _UniverseEntry(package=package.name, is_project_source=is_root)
        return universe

    def _match(
        self,
        source: BundleHandle,
        ref: NodeReference,
        universes: dict[str, dict[str, _UniverseEntry]],
        imports_resolved: dict[str, dict[str, list[str]]],
        imports_unresolved: dict[str, dict[str, list[str]]],
        counters: _Counters,
    ) -> CrossLinkEdge | None:
        """Rule-B exact match against SIBLING universes, Rule-C on miss."""
        siblings = [b for b in self.bundles if b.project != source.project]
        candidates: list[tuple[BundleHandle, str, _UniverseEntry]] = []
        for sibling in siblings:
            entry = universes[sibling.project].get(ref.to_name)
            if entry is not None:
                candidates.append((sibling, ref.to_name, entry))
        if not candidates:
            return self._match_alias(
                source, ref, siblings, universes, imports_resolved, imports_unresolved, counters
            )
        if len(candidates) > 1:
            counters.collisions[source.project] = counters.collisions.get(source.project, 0) + 1
        # Collision precedence (spec §3.3 step 4): project source beats
        # dependency copies; ties break by bundle indexed_at recency; exactly
        # ONE edge per (from, to_name, kind).
        winner = max(
            candidates,
            key=lambda c: (c[2].is_project_source, c[0].indexed_at, c[0].project),
        )
        return self._emit(source, ref, winner[0], winner[1], counters)

    def _match_alias(
        self,
        source: BundleHandle,
        ref: NodeReference,
        siblings: list[BundleHandle],
        universes: dict[str, dict[str, _UniverseEntry]],
        imports_resolved: dict[str, dict[str, list[str]]],
        imports_unresolved: dict[str, dict[str, list[str]]],
        counters: _Counters,
    ) -> CrossLinkEdge | None:
        """Rule-C (spec §A1.3): resolve one re-export hop via the sibling's
        own persisted IMPORTS graph — absolute AND relative styles."""
        if self.alias_resolution != "imports_graph" or "." not in ref.to_name:
            return None
        module, leaf = ref.to_name.rsplit(".", 1)
        targets: set[tuple[str, str]] = set()  # (sibling project, resolved qname)
        for sibling in siblings:
            universe = universes[sibling.project]
            if module not in universe:
                continue
            targets |= _alias_targets(
                module,
                leaf,
                universe,
                imports_resolved[sibling.project],
                imports_unresolved[sibling.project],
                sibling.project,
            )
        if len(targets) > 1:
            counters.alias["ambiguous"] += 1
            return None
        if not targets:
            return None
        project_name, qname = next(iter(targets))
        sibling = next(b for b in siblings if b.project == project_name)
        counters.alias["resolved"] += 1
        return self._emit(source, ref, sibling, qname, counters)

    def _emit(
        self,
        source: BundleHandle,
        ref: NodeReference,
        target: BundleHandle,
        to_node_id: str,
        counters: _Counters,
    ) -> CrossLinkEdge:
        counters.edges_created[source.project] = counters.edges_created.get(source.project, 0) + 1
        return CrossLinkEdge(
            from_project=source.project,
            from_package=ref.from_package,
            from_node_id=ref.from_node_id,
            to_project=target.project,
            to_node_id=to_node_id,
            to_name=ref.to_name,
            kind=ref.kind,
        )


def _collect_qnames(node: DocumentNode, out: set[str]) -> None:
    """Walk a DocumentNode tree, collecting every qualified_name."""
    out.add(node.qualified_name)
    for child in node.children:
        _collect_qnames(child, out)


def _edge_sort_key(e: CrossLinkEdge) -> tuple[str, str, str, str, str]:
    return (e.from_project, e.from_node_id, e.to_project, e.to_node_id, str(e.kind))


def _alias_targets(
    module: str,
    leaf: str,
    universe: dict[str, _UniverseEntry],
    resolved_imports: dict[str, list[str]],
    unresolved_imports: dict[str, list[str]],
    project: str,
) -> set[tuple[str, str]]:
    """Rule-C candidates for one sibling: absolute + relative re-exports."""
    targets: set[tuple[str, str]] = set()
    for to_id in resolved_imports.get(module, ()):
        if to_id.rsplit(".", 1)[-1] == leaf and to_id in universe:
            targets.add((project, to_id))
    # Relative re-exports (`from .core import fn` persists "core.fn" with the
    # level dropped): mini-resolve against the sibling's OWN universe under
    # the module's parent prefix.
    if "." in module:
        parent = module.rsplit(".", 1)[0]
        for to_name in unresolved_imports.get(module, ()):
            if to_name.rsplit(".", 1)[-1] != leaf:
                continue
            candidate = f"{parent}.{to_name}"
            if candidate in universe:
                targets.add((project, candidate))
    return targets


def detect_stale(
    bundles: tuple[BundleHandle, ...],
    stamps: tuple[LinkedBundleStamp, ...],
) -> frozenset[str]:
    """Projects whose overlay stamps no longer match their bundles (spec §3.8).

    Missing stamp, ``indexed_at`` mismatch, or ``git_head`` mismatch (when
    both sides carry one) ⇒ stale. Departed bundles are handled separately
    by the linker's purge.
    """
    by_project = {stamp.project_name: stamp for stamp in stamps}
    stale: set[str] = set()
    for bundle in bundles:
        stamp = by_project.get(bundle.project)
        if stamp is None or stamp.indexed_at != bundle.indexed_at:
            stale.add(bundle.project)
            continue
        if stamp.git_head and bundle.git_head and stamp.git_head != bundle.git_head:
            stale.add(bundle.project)
    return frozenset(stale)


def _try_pagerank(edges: list[tuple[str, str]]) -> tuple[dict[str, float], bool]:
    """PageRank over composite node ids; NULL-degrade when [graph] is absent.

    Reuses the per-bundle computation (generic over node-id strings) but
    NEVER lets its ImportError escape — the in_degree tier must always land
    (spec §A1.1). One warning; ``False`` flips LinkReport.pagerank_available.
    """
    if not edges:
        return {}, True
    try:
        from pydocs_mcp.application.node_score_compute import compute_scores

        qname_projects = {node: node.split(":", 1)[0] for pair in edges for node in pair}
        scores = compute_scores(edges, qname_projects)
        return {score.qualified_name: score.pagerank for score in scores}, True
    except ImportError:
        logger.warning(
            "workspace pagerank skipped: the [graph] extra is not installed "
            "(pip install 'pydocs-mcp[graph]'); in_degree scores still computed"
        )
        return {}, False
