"""WorkspaceLinker — Rule-B/Rule-C matching, precedence, batching (AC4-9, 28-30, 32).

Two-plus bundles are simulated with the in-memory stores behind
``make_fake_uow_factory`` (spec §5 fixture guidance for application-layer
tests); the overlay is the ``InMemoryCrossLinkStore``. Sibling packages are
NEVER importable in this test environment — the G4 discipline.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.workspace_linker import (
    BundleHandle,
    LinkReport,
    WorkspaceLinker,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.node_reference import NodeReference

from ._fakes import (
    InMemoryDocumentTreeStore,
    InMemoryPackageStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

_KINDS = (
    ReferenceKind.CALLS,
    ReferenceKind.IMPORTS,
    ReferenceKind.INHERITS,
    ReferenceKind.GOVERNS,
)


def _node(qname: str, *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=f"{qname.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text="x",
        content_hash=f"h-{qname}",
        children=tuple(children),
    )


def _package(name: str, origin: PackageOrigin = PackageOrigin.PROJECT) -> Package:
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash=f"ph-{name}",
        origin=origin,
    )


def _ref(
    from_node_id: str,
    to_name: str,
    *,
    kind: ReferenceKind = ReferenceKind.CALLS,
    to_node_id: str | None = None,
    from_package: str = "__project__",
) -> NodeReference:
    return NodeReference(
        from_package=from_package,
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


def _bundle(
    project: str,
    *,
    packages: dict[str, tuple[DocumentNode, ...]],
    refs: tuple[NodeReference, ...] = (),
    indexed_at: float = 1000.0,
    project_source_packages: frozenset[str] = frozenset({"__project__"}),
) -> BundleHandle:
    package_store = InMemoryPackageStore()
    tree_store = InMemoryDocumentTreeStore()
    ref_store = InMemoryReferenceStore()
    for name, trees in packages.items():
        origin = (
            PackageOrigin.PROJECT if name in project_source_packages else PackageOrigin.DEPENDENCY
        )
        package_store.items[name] = _package(name, origin)
        tree_store.by_package.setdefault(name, []).extend(trees)
    for ref in refs:
        ref_store.by_package.setdefault(ref.from_package, []).append(ref)
    factory = make_fake_uow_factory(packages=package_store, trees=tree_store, references=ref_store)
    return BundleHandle(
        project=project,
        bundle_stem=f"{project}_stem",
        bundle_path=f"/bundles/{project}.db",
        indexed_at=indexed_at,
        git_head="head",
        uow_factory=factory,
    )


def _linker(
    *bundles: BundleHandle,
    store: InMemoryCrossLinkStore | None = None,
    match_scope: str = "project_only",
    alias_resolution: str = "imports_graph",
) -> tuple[WorkspaceLinker, InMemoryCrossLinkStore]:
    store = store or InMemoryCrossLinkStore()
    linker = WorkspaceLinker(
        bundles=bundles,
        cross_links=store,
        kinds=_KINDS,
        match_scope=match_scope,  # type: ignore[arg-type]
        alias_resolution=alias_resolution,  # type: ignore[arg-type]
    )
    return linker, store


async def test_rule_b_links_unresolved_to_sibling_project_source() -> None:
    # AC4: repo B's package is NOT installed here — linking works on bundles.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa", _node("repoa.api.handler")),)},
        refs=(_ref("repoa.api.handler", "repob.mod.fn"),),
    )
    repob = _bundle("repob", packages={"__project__": (_node("repob", _node("repob.mod.fn")),)})
    linker, store = _linker(repoa, repob)
    report = await linker.link()
    edges = await store.edges_into("repob", "repob.mod.fn")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.from_project == "repoa" and edge.to_project == "repob"
    assert edge.to_node_id == "repob.mod.fn" and str(edge.kind) == "calls"
    assert report.edges_created["repoa"] == 1
    assert report.unresolved_scanned["repoa"] == 1


async def test_bundle_never_links_to_itself() -> None:
    # AC5: an unresolved name matching only the SAME bundle → no edge.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa", _node("repoa.core.fn")),)},
        refs=(_ref("repoa.api.handler", "repoa.core.fn"),),
    )
    repob = _bundle("repob", packages={"__project__": (_node("repob"),)})
    linker, store = _linker(repoa, repob)
    report = await linker.link()
    assert await store.edges_from("repoa", "repoa.api.handler") == ()
    assert report.edges_created == {}


async def test_collision_precedence_project_source_beats_dependency() -> None:
    # AC6: sibling B exports shared.mod.fn as project source; sibling C only
    # carries it as a dependency copy — B wins; exactly one edge.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa"),)},
        refs=(_ref("repoa.x", "shared.mod.fn"),),
    )
    repob = _bundle(
        "repob",
        packages={"__project__": (_node("shared", _node("shared.mod.fn")),)},
        indexed_at=500.0,
    )
    repoc = _bundle(
        "repoc",
        packages={
            "__project__": (_node("repoc"),),
            "shared": (_node("shared", _node("shared.mod.fn")),),
        },
        indexed_at=2000.0,
    )
    linker, store = _linker(repoa, repob, repoc, match_scope="all_packages")
    report = await linker.link()
    edges = await store.edges_from("repoa", "repoa.x")
    assert len(edges) == 1
    assert edges[0].to_project == "repob"  # project source beats newer dep copy
    assert report.collisions["repoa"] == 1


async def test_collision_ties_break_by_recency() -> None:
    repoa = _bundle(
        "repoa", packages={"__project__": (_node("repoa"),)}, refs=(_ref("repoa.x", "pkg.fn"),)
    )
    older = _bundle(
        "older", packages={"__project__": (_node("pkg", _node("pkg.fn")),)}, indexed_at=100.0
    )
    newer = _bundle(
        "newer", packages={"__project__": (_node("pkg", _node("pkg.fn")),)}, indexed_at=200.0
    )
    linker, store = _linker(repoa, older, newer)
    await linker.link()
    (edge,) = await store.edges_from("repoa", "repoa.x")
    assert edge.to_project == "newer"


async def test_match_scope_project_only_ignores_dependency_universes() -> None:
    # AC7: with the default scope, a sibling's dependency copy is invisible;
    # all_packages links it.
    repoa = _bundle(
        "repoa", packages={"__project__": (_node("repoa"),)}, refs=(_ref("repoa.x", "dep.fn"),)
    )
    repob = _bundle(
        "repob",
        packages={
            "__project__": (_node("repob"),),
            "dep": (_node("dep", _node("dep.fn")),),
        },
    )
    linker, store = _linker(repoa, repob)
    await linker.link()
    assert await store.edges_from("repoa", "repoa.x") == ()
    linker_all, store_all = _linker(repoa, repob, match_scope="all_packages")
    await linker_all.link()
    assert len(await store_all.edges_from("repoa", "repoa.x")) == 1


async def test_kinds_filter_excludes_unlisted_kinds() -> None:
    # AC8: mentions rows are never scanned with the default kinds.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa"),)},
        refs=(_ref("repoa.x", "repob.fn", kind=ReferenceKind.MENTIONS),),
    )
    repob = _bundle("repob", packages={"__project__": (_node("repob", _node("repob.fn")),)})
    linker, store = _linker(repoa, repob)
    await linker.link()
    assert await store.edges_from("repoa", "repoa.x") == ()
    with_mentions = WorkspaceLinker(
        bundles=(repoa, repob),
        cross_links=store,
        kinds=(*_KINDS, ReferenceKind.MENTIONS),
        match_scope="project_only",
        alias_resolution="imports_graph",
    )
    await with_mentions.link()
    assert len(await store.edges_from("repoa", "repoa.x")) == 1


async def test_relative_import_shapes_ac9() -> None:
    # AC9: bare to_name ("x") never matches; dotted relative ("sub.x") links
    # iff a sibling genuinely exports top-level sub.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa"),)},
        refs=(_ref("repoa.m", "x"), _ref("repoa.m2", "sub.x")),
    )
    repob = _bundle("repob", packages={"__project__": (_node("sub", _node("sub.x")),)})
    linker, store = _linker(repoa, repob)
    report = await linker.link()
    assert await store.edges_from("repoa", "repoa.m") == ()  # bare name: no edge, no error
    (edge,) = await store.edges_from("repoa", "repoa.m2")  # documented precision trade-off
    assert edge.to_node_id == "sub.x"
    assert isinstance(report, LinkReport)


async def test_governs_rows_link_by_default() -> None:
    # AC26 write half: decision GOVERNS edges ride the same machinery.
    repoa = _bundle(
        "repoa",
        packages={"__project__": (_node("repoa"),)},
        refs=(_ref("decision:use-parser", "repob.core.parse", kind=ReferenceKind.GOVERNS),),
    )
    repob = _bundle("repob", packages={"__project__": (_node("repob", _node("repob.core.parse")),)})
    linker, store = _linker(repoa, repob)
    await linker.link()
    (edge,) = await store.edges_into("repob", "repob.core.parse")
    assert edge.from_node_id == "decision:use-parser" and str(edge.kind) == "governs"


class TestRuleC:
    def _repob_with_reexport(self, *, relative: bool) -> BundleHandle:
        # repob/api.py re-exports fn from repob/core.py.
        import_row = (
            _ref("repob.api", "core.fn", kind=ReferenceKind.IMPORTS)  # from .core import fn
            if relative
            else _ref(
                "repob.api", "repob.core.fn", kind=ReferenceKind.IMPORTS, to_node_id="repob.core.fn"
            )
        )
        return _bundle(
            "repob",
            packages={
                "__project__": (
                    _node("repob", _node("repob.api"), _node("repob.core", _node("repob.core.fn"))),
                )
            },
            refs=(import_row,),
        )

    async def test_absolute_reexport_resolves_to_the_real_target(self) -> None:
        # AC28(a): with repo B not installed (G4 discipline).
        repoa = _bundle(
            "repoa",
            packages={"__project__": (_node("repoa"),)},
            refs=(_ref("repoa.x", "repob.api.fn"),),
        )
        linker, store = _linker(repoa, self._repob_with_reexport(relative=False))
        report = await linker.link()
        (edge,) = await store.edges_from("repoa", "repoa.x")
        assert edge.to_node_id == "repob.core.fn"
        assert edge.to_name == "repob.api.fn"  # audit keeps the original
        assert report.alias_resolved == 1

    async def test_relative_reexport_resolves_to_the_real_target(self) -> None:
        # AC28(b): the dominant style — B's own row is UNRESOLVED "core.fn".
        repoa = _bundle(
            "repoa",
            packages={"__project__": (_node("repoa"),)},
            refs=(_ref("repoa.x", "repob.api.fn"),),
        )
        linker, store = _linker(repoa, self._repob_with_reexport(relative=True))
        report = await linker.link()
        (edge,) = await store.edges_from("repoa", "repoa.x")
        assert edge.to_node_id == "repob.core.fn"
        assert report.alias_resolved == 1

    async def test_ambiguous_alias_emits_nothing(self) -> None:
        # AC29: two distinct resolved targets sharing the leaf → no edge.
        repoa = _bundle(
            "repoa",
            packages={"__project__": (_node("repoa"),)},
            refs=(_ref("repoa.x", "repob.api.fn"),),
        )
        repob = _bundle(
            "repob",
            packages={
                "__project__": (
                    _node(
                        "repob",
                        _node("repob.api"),
                        _node("repob.core", _node("repob.core.fn")),
                        _node("repob.alt", _node("repob.alt.fn")),
                    ),
                )
            },
            refs=(
                _ref(
                    "repob.api",
                    "repob.core.fn",
                    kind=ReferenceKind.IMPORTS,
                    to_node_id="repob.core.fn",
                ),
                _ref(
                    "repob.api",
                    "repob.alt.fn",
                    kind=ReferenceKind.IMPORTS,
                    to_node_id="repob.alt.fn",
                ),
            ),
        )
        linker, store = _linker(repoa, repob)
        report = await linker.link()
        assert await store.edges_from("repoa", "repoa.x") == ()
        assert report.alias_ambiguous == 1

    async def test_alias_resolution_off_restores_rule_b_only(self) -> None:
        # AC30: the A/B knob.
        repoa = _bundle(
            "repoa",
            packages={"__project__": (_node("repoa"),)},
            refs=(_ref("repoa.x", "repob.api.fn"),),
        )
        linker, store = _linker(
            repoa, self._repob_with_reexport(relative=False), alias_resolution="off"
        )
        report = await linker.link()
        assert await store.edges_from("repoa", "repoa.x") == ()
        assert report.alias_resolved == 0


async def test_locally_resolved_rows_are_never_scanned_ac32() -> None:
    # AC32: repo A resolved the ref at index time (sibling installed) — the
    # linker must not scan it, even though repo B also exports the qname.
    repoa = _bundle(
        "repoa",
        packages={
            "__project__": (_node("repoa"),),
            "shared": (_node("shared", _node("shared.fn")),),
        },
        refs=(_ref("repoa.x", "shared.fn", to_node_id="shared.fn"),),  # RESOLVED locally
    )
    repob = _bundle("repob", packages={"__project__": (_node("shared", _node("shared.fn")),)})
    linker, store = _linker(repoa, repob, match_scope="all_packages")
    report = await linker.link()
    assert await store.edges_from("repoa", "repoa.x") == ()
    assert report.unresolved_scanned["repoa"] == 0
    assert report.edges_created == {}


async def test_incremental_write_replaces_only_stale_touching_edges() -> None:
    # §3.8 write semantics: only stale-touching edge sets are swapped.
    store = InMemoryCrossLinkStore()
    repoa = _bundle(
        "repoa", packages={"__project__": (_node("repoa"),)}, refs=(_ref("repoa.x", "repob.fn"),)
    )
    repob = _bundle("repob", packages={"__project__": (_node("repob", _node("repob.fn")),)})
    repoc = _bundle(
        "repoc", packages={"__project__": (_node("repoc"),)}, refs=(_ref("repoc.y", "repob.fn"),)
    )
    linker, _ = _linker(repoa, repob, repoc, store=store)
    await linker.link()  # full pass
    stamps_before = {s.project_name: s.linked_at for s in await store.bundle_stamps()}
    await linker.link(stale_projects=frozenset({"repoa"}))
    assert len(await store.edges_into("repob", "repob.fn")) == 2  # both survive
    stamps_after = {s.project_name: s.linked_at for s in await store.bundle_stamps()}
    assert stamps_after["repoa"] >= stamps_before["repoa"]
    assert stamps_after["repoc"] == stamps_before["repoc"]  # untouched stamp


@pytest.mark.parametrize("full", [True, False])
async def test_stamps_written_for_stale_projects(full: bool) -> None:
    repoa = _bundle("repoa", packages={"__project__": (_node("repoa"),)})
    repob = _bundle("repob", packages={"__project__": (_node("repob"),)})
    linker, store = _linker(repoa, repob)
    await linker.link(None if full else frozenset({"repoa"}))
    stamps = {s.project_name for s in await store.bundle_stamps()}
    assert stamps == ({"repoa", "repob"} if full else {"repoa"})
