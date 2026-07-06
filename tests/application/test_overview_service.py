"""OverviewService tests — the structural orientation card (spec §D17 blocks 1, 3-7).

Seeds in-memory fakes via ``make_fake_uow_factory`` and asserts on the returned
``OverviewCard`` value object (not rendered text): stats, centrality-ranked
module map, entry points, communities, dependency profile, doc coverage.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryNodeScoreStore,
    InMemoryPackageStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

_PKG = "__project__"


def _package(name: str, origin: PackageOrigin) -> Package:
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


def _module_node(qname: str, text: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=qname.replace(".", "/") + ".py",
        start_line=1,
        end_line=10,
        text=text,
        content_hash="h",
    )


def _member(name: str, *, docstring: str) -> ModuleMember:
    return ModuleMember(metadata={"package": _PKG, "name": name, "docstring": docstring})


def _score(qname: str, *, pagerank: float, community: int, in_degree: int = 0) -> NodeScore:
    return NodeScore(
        package=_PKG,
        qualified_name=qname,
        in_degree=in_degree,
        pagerank=pagerank,
        community=community,
    )


def _edge(frm: str, to: str, kind: ReferenceKind = ReferenceKind.CALLS) -> NodeReference:
    """A resolved directed edge ``frm`` → ``to`` in the project package."""
    return NodeReference(
        from_package=_PKG,
        from_node_id=frm,
        to_name=to,
        to_node_id=to,
        kind=kind,
    )


def _seed_stores() -> dict:
    """Shared seed: 2 packages, 4-5 project module trees, members with/without
    docstrings, node_scores across two communities, CALLS + IMPORTS edges."""
    packages = InMemoryPackageStore(
        items={
            _PKG: _package(_PKG, PackageOrigin.PROJECT),
            "numpy": _package("numpy", PackageOrigin.DEPENDENCY),
        }
    )

    trees = InMemoryDocumentTreeStore()
    trees.by_package[_PKG] = [
        _module_node("proj.core", "Core module.\nmore prose"),
        _module_node("proj.api", "API layer."),
        _module_node("proj.cli.main", "CLI entry."),
        _module_node("proj.__main__", "Dunder-main runner."),
        # test-marker module: matches the dunder-main rule but is excluded.
        _module_node("proj.tests.__main__", "Test runner."),
    ]

    members = InMemoryModuleMemberStore()
    members.by_package[_PKG] = [
        _member("proj.core.f", docstring="documented"),
        _member("proj.api.g", docstring="also documented"),
        _member("proj.cli.h", docstring=""),  # undocumented → 2/3 coverage
    ]

    node_scores = InMemoryNodeScoreStore()
    scores = (
        _score("proj.core", pagerank=0.9, community=1, in_degree=1),
        _score("proj.core.helpers", pagerank=0.3, community=1, in_degree=1),
        _score("proj.api", pagerank=0.7, community=2, in_degree=2),
        _score("proj.cli.main", pagerank=0.1, community=-1),  # unassigned → skipped
    )

    references = InMemoryReferenceStore()

    return {
        "packages": packages,
        "trees": trees,
        "members": members,
        "node_scores": node_scores,
        "scores": scores,
        "references": references,
    }


async def _seed_references(references: InMemoryReferenceStore) -> None:
    await references.save_many(
        [
            _edge("proj.core", "proj.core.helpers"),  # intra-community (1→1)
            _edge("proj.core", "proj.api"),  # cross-community (1→2)
            _edge("proj.core", "numpy.array", ReferenceKind.IMPORTS),
            _edge("proj.api", "numpy.linalg.solve", ReferenceKind.IMPORTS),
            _edge("proj.core", "pydantic.BaseModel", ReferenceKind.IMPORTS),
            _edge("proj.cli.main", "proj.core"),  # root: in_degree 0, high out_degree
            _edge("proj.cli.main", "proj.api"),
        ],
        package=_PKG,
    )


def _build_service(*, with_scores: bool = True, **overrides) -> OverviewService:
    seed = _seed_stores()
    node_scores = seed["node_scores"]
    if with_scores:
        asyncio.run(node_scores.upsert(seed["scores"]))
    asyncio.run(_seed_references(seed["references"]))
    factory = make_fake_uow_factory(
        packages=seed["packages"],
        trees=seed["trees"],
        module_members=seed["members"],
        node_scores=node_scores,
        references=seed["references"],
    )
    return OverviewService(
        uow_factory=factory, scripts={"demo-cli": "demo.__main__:main"}, **overrides
    )


def test_card_stats_and_module_map_ranked_by_pagerank() -> None:
    service = _build_service()
    card = asyncio.run(service.build(package=_PKG))
    assert card.package_count == 2
    assert [m.qualified_name for m in card.modules][:2] == ["proj.core", "proj.api"]
    assert card.doc_coverage == pytest.approx(2 / 3)
    assert card.node_scores_available is True


def test_module_map_falls_back_to_in_degree_without_scores() -> None:
    # node_scores empty → ranking uses degree_by_package in-degree.
    service = _build_service(with_scores=False)
    card = asyncio.run(service.build(package=_PKG))
    assert card.node_scores_available is False
    # proj.api has in_degree 2 (top), proj.core in_degree 1.
    assert card.modules[0].qualified_name == "proj.api"
    assert card.communities == ()  # no scores → no communities


def test_entry_points_union_scripts_dunder_main_and_roots() -> None:
    service = _build_service()
    card = asyncio.run(service.build(package=_PKG))
    kinds = {(e.name, e.kind) for e in card.entry_points}
    assert ("demo-cli", "script") in kinds
    assert ("proj.__main__", "module") in kinds
    assert ("proj.cli.main", "root") in kinds  # zero in-degree, out-degree above median
    assert all("test" not in e.name for e in card.entry_points)


def test_communities_labeled_by_shared_prefix_with_cohesion() -> None:
    service = _build_service()
    card = asyncio.run(service.build(package=_PKG))
    top = card.communities[0]
    assert top.label == "proj.core" and top.size == 2 and 0.0 <= top.cohesion <= 1.0


def test_dependency_profile_from_imports() -> None:
    service = _build_service()
    card = asyncio.run(service.build(package=_PKG))
    assert card.dependency_profile[0] == ("numpy", 2)


def test_caps_respected() -> None:
    service = _build_service(max_modules=1, max_communities=1)
    card = asyncio.run(service.build(package=_PKG))
    assert len(card.modules) == 1 and len(card.communities) == 1


def test_dependency_profile_excludes_project_self_imports() -> None:
    """Self-imports leak past the storage-layer ``top == package`` guard because
    the primary target is the ``__project__`` sentinel while the project's own
    import targets use the REAL top-level name (``proj``). OverviewService must
    derive ``proj`` from the tree module qnames and filter it out."""
    seed = _seed_stores()
    asyncio.run(seed["node_scores"].upsert(seed["scores"]))
    asyncio.run(_seed_references(seed["references"]))
    # A project-internal import: proj.core imports proj.util (top segment proj).
    asyncio.run(
        seed["references"].save_many(
            [_edge("proj.core", "proj.util", ReferenceKind.IMPORTS)],
            package=_PKG,
        )
    )
    factory = make_fake_uow_factory(
        packages=seed["packages"],
        trees=seed["trees"],
        module_members=seed["members"],
        node_scores=seed["node_scores"],
        references=seed["references"],
    )
    service = OverviewService(uow_factory=factory, scripts={})
    card = asyncio.run(service.build(package=_PKG))
    names = {name for name, _ in card.dependency_profile}
    assert "numpy" in names
    assert "proj" not in names


def test_entry_points_dedup_dunder_main_that_is_also_a_root() -> None:
    """A ``*.__main__`` module that also qualifies as a zero-in-degree /
    high-out-degree CALLS root must appear exactly once, as ``module`` (the
    more specific/declared kind wins over the inferred ``root``)."""
    seed = _seed_stores()
    asyncio.run(seed["node_scores"].upsert(seed["scores"]))
    asyncio.run(_seed_references(seed["references"]))
    # proj.__main__ calls three nodes → zero in-degree, out-degree above the
    # candidate median, so it ALSO qualifies as a graph root alongside being a
    # dunder-main module.
    asyncio.run(
        seed["references"].save_many(
            [
                _edge("proj.__main__", "proj.core"),
                _edge("proj.__main__", "proj.api"),
                _edge("proj.__main__", "proj.cli.main"),
            ],
            package=_PKG,
        )
    )
    factory = make_fake_uow_factory(
        packages=seed["packages"],
        trees=seed["trees"],
        module_members=seed["members"],
        node_scores=seed["node_scores"],
        references=seed["references"],
    )
    service = OverviewService(uow_factory=factory, scripts={})
    card = asyncio.run(service.build(package=_PKG))
    dunder = [e for e in card.entry_points if e.name == "proj.__main__"]
    assert len(dunder) == 1
    assert dunder[0].kind == "module"
