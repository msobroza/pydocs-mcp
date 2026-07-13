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

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.cross_link_edge import CrossLinkEdge, LinkedBundleStamp
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import CrossLinkStore, UnitOfWork


@dataclass(frozen=True, slots=True)
class BundleHandle:
    """One loaded bundle as the linker sees it (identity + read-only access)."""

    project: str
    bundle_stem: str
    bundle_path: str
    indexed_at: float
    git_head: str | None
    uow_factory: Callable[[], UnitOfWork]


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

        linked_at = time.time()
        for bundle in sorted(self.bundles, key=lambda b: b.project):
            if bundle.project not in stale:
                continue
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
        return LinkReport(
            unresolved_scanned=dict(counters.unresolved_scanned),
            edges_created=dict(counters.edges_created),
            collisions=dict(counters.collisions),
            alias_resolved=counters.alias["resolved"],
            alias_ambiguous=counters.alias["ambiguous"],
        )

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
