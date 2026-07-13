"""ReferenceService cross-link unions — callers/callees/inherits/governed_by
(AC11, AC12, AC33 read-side dedup; NullCrossLinkStore regression half of AC17).
"""

from __future__ import annotations

from pydocs_mcp.application.reference_service import CrossReferenceRow, ReferenceService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import CrossLinkEdge
from pydocs_mcp.storage.in_memory_cross_link_store import InMemoryCrossLinkStore
from pydocs_mcp.storage.node_reference import NodeReference

from .._fakes import InMemoryReferenceStore, make_fake_uow_factory


def _local(
    from_node_id: str,
    to_name: str,
    *,
    kind: ReferenceKind = ReferenceKind.CALLS,
    to_node_id: str | None = None,
) -> NodeReference:
    return NodeReference(
        from_package="__project__",
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


def _edge(
    *,
    from_project: str = "repoa",
    from_node_id: str = "repoa.api.handler",
    to_project: str = "repob",
    to_node_id: str = "repob.core.parse",
    to_name: str | None = None,
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> CrossLinkEdge:
    return CrossLinkEdge(
        from_project=from_project,
        from_package="__project__",
        from_node_id=from_node_id,
        to_project=to_project,
        to_node_id=to_node_id,
        to_name=to_name if to_name is not None else to_node_id,
        kind=kind,
    )


def _service(
    *,
    project: str = "repob",
    local_rows: tuple[NodeReference, ...] = (),
) -> tuple[ReferenceService, InMemoryCrossLinkStore]:
    store = InMemoryCrossLinkStore()
    service = ReferenceService(
        uow_factory=make_fake_uow_factory(references=_seeded_store(local_rows)),
        project_name=project,
        cross_links=store,
    )
    return service, store


async def test_callers_unions_local_and_overlay_rows_local_first() -> None:
    # AC11: local callers ∪ overlay edges_into, local rows FIRST (A1.8).
    local = _local("repob.utils.helper", "repob.core.parse", to_node_id="repob.core.parse")
    service, store = _service(local_rows=(local,))
    await store.replace_edges_touching("repoa", (_edge(),))
    rows = await service.callers("__project__", "repob.core.parse")
    assert len(rows) == 2
    assert rows[0] is local  # bundle-local first
    assert isinstance(rows[1], CrossReferenceRow)
    assert rows[1].from_project == "repoa"


async def test_callers_with_null_store_is_byte_identical() -> None:
    # AC11 second half / AC17 seed: NullCrossLinkStore degenerates to today.
    local = _local("repob.utils.helper", "repob.core.parse", to_node_id="repob.core.parse")
    plain = ReferenceService(
        uow_factory=make_fake_uow_factory(
            references=_seeded_store((local,)),
        )
    )
    rows = await plain.callers("__project__", "repob.core.parse")
    assert rows == (local,)


def _seeded_store(rows: tuple[NodeReference, ...]) -> InMemoryReferenceStore:
    refs = InMemoryReferenceStore()
    for row in rows:
        refs.by_package.setdefault(row.from_package, []).append(row)
    return refs


async def test_callees_substitutes_matching_unresolved_rows() -> None:
    # AC12: unresolved local row + matching edges_from → resolved cross row;
    # a non-matching unresolved row stays unresolved.
    matched = _local("repoa.api.handler", "repob.core.parse")
    unmatched = _local("repoa.api.handler", "ghost.fn")
    service, store = _service(project="repoa", local_rows=(matched, unmatched))
    await store.replace_edges_touching("repoa", (_edge(),))
    rows = await service.callees("__project__", "repoa.api.handler")
    assert len(rows) == 2
    substituted = [r for r in rows if isinstance(r, CrossReferenceRow)]
    assert len(substituted) == 1
    assert substituted[0].to_node_id == "repob.core.parse"
    assert substituted[0].to_project == "repob"
    still_unresolved = [r for r in rows if not isinstance(r, CrossReferenceRow)]
    assert still_unresolved[0].to_name == "ghost.fn"
    assert still_unresolved[0].to_node_id is None


async def test_callees_stale_edge_never_shadows_a_locally_resolved_row() -> None:
    # AC33: local resolved row + stale overlay edge for the same
    # (from, to, kind) → the local row wins, no duplicate.
    resolved = _local("repoa.api.handler", "repob.core.parse", to_node_id="repob.core.parse")
    service, store = _service(project="repoa", local_rows=(resolved,))
    await store.replace_edges_touching("repoa", (_edge(),))
    rows = await service.callees("__project__", "repoa.api.handler")
    assert rows == (resolved,)  # exactly once, the local row


async def test_inherits_union_appends_cross_subclasses() -> None:
    local = _local(
        "repob.models.Child",
        "repob.models.Base",
        kind=ReferenceKind.INHERITS,
        to_node_id="repob.models.Base",
    )
    service, store = _service(local_rows=(local,))
    await store.replace_edges_touching(
        "repoa",
        (
            _edge(
                from_node_id="repoa.ext.OtherChild",
                to_node_id="repob.models.Base",
                kind=ReferenceKind.INHERITS,
            ),
        ),
    )
    rows = await service.inherits("__project__", "repob.models.Base")
    assert [type(r) for r in rows] == [NodeReference, CrossReferenceRow]


async def test_governed_by_union_includes_sibling_decisions() -> None:
    # AC26(a): the governed_by union surfaces the repo-A decision edge.
    service, store = _service()
    await store.replace_edges_touching(
        "repoa",
        (
            _edge(
                from_node_id="decision:use-parser",
                to_node_id="repob.core.parse",
                kind=ReferenceKind.GOVERNS,
            ),
        ),
    )
    rows = await service.governed_by("__project__", "repob.core.parse")
    assert len(rows) == 1
    assert isinstance(rows[0], CrossReferenceRow)
    assert rows[0].from_node_id == "decision:use-parser"


async def test_callers_dedup_prefers_local_on_exact_duplicate() -> None:
    # AC33 callers half: an overlay edge byte-duplicating a local resolved
    # row is suppressed.
    local = _local("repoa.api.handler", "repob.core.parse", to_node_id="repob.core.parse")
    service, store = _service(local_rows=(local,))
    await store.replace_edges_touching("repoa", (_edge(),))
    rows = await service.callers("__project__", "repob.core.parse")
    cross = [r for r in rows if isinstance(r, CrossReferenceRow)]
    assert cross == []  # the duplicate edge was suppressed
    assert rows == (local,)
