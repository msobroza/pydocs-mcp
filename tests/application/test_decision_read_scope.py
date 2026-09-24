"""Dependency decisions answer only when asked (#346).

With ``decision_capture.include_deps: true`` a dependency's decisions persist
under its own package. They answer ``search_codebase(kind="decision")`` when the
request names them — ``scope="deps"`` or ``package=<dep>`` — and
``get_why(targets=...)`` on a dependency symbol. ``get_why(query)``, the
dashboard and the default decision search stay on the project, byte for byte.

Every decision search pushes a ``package`` pre-filter (both retrieval branches
honour ``package``; only the BM25 fetcher honours ``scope``):

=====================  ==============================================
request                package pre-filter
=====================  ==============================================
``package=X``          ``X`` (wins over ``scope``)
``scope="project"``    ``__project__``
``scope="deps"``       ``{"in": [dependency packages with records]}``
``scope="all"``        ``__project__`` (the default: unchanged output)
=====================  ==============================================
"""

from __future__ import annotations

import inspect

import pytest

from pydocs_mcp.application.decision_service import DecisionService
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.protocols import DecisionNavigator
from pydocs_mcp.application.suggestions import SEARCH_ZERO_HIT_SUGGESTION
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkFilterField, SearchScope
from tests._fakes import InMemoryReferenceStore

from ._decision_fakes import (
    DECISION_PRESET_ROWS,
    FixedHitsDocs,
    PreFilteringDocs,
    decision_chunk,
    decision_record,
    governs_edge,
    make_decision_service,
)

_PROJECT = PROJECT_PACKAGE_NAME
REC_SIDECAR = decision_record(id=1, title="Use SQLite sidecar")
REC_CACHE = decision_record(id=2, title="Use redis cache", status="proposed")
REC_POOL = decision_record(id=3, title="Pool connections per host", package="requests")
REC_SLOTS = decision_record(id=4, title="Slots on every class", package="attrs")
_EVERY_RECORD = (REC_SIDECAR, REC_CACHE, REC_POOL, REC_SLOTS)


def _pushed_package(docs: FixedHitsDocs) -> object:
    (query,) = docs.queries
    return query.pre_filter[ChunkFilterField.PACKAGE.value]


# ── the corpus mapping ───────────────────────────────────────────────────


async def test_get_why_query_pushes_the_project_package_next_to_the_origin() -> None:
    docs = FixedHitsDocs(hits=(decision_chunk(REC_SIDECAR),))
    await make_decision_service(records=_EVERY_RECORD, docs=docs).why_search("why sidecar")
    (query,) = docs.queries
    assert query.pre_filter == {"origin": "decision_record", "package": _PROJECT}


@pytest.mark.parametrize(
    ("scope", "package", "pushed"),
    [
        (SearchScope.ALL, "", _PROJECT),
        (SearchScope.PROJECT_ONLY, "", _PROJECT),
        (SearchScope.DEPENDENCIES_ONLY, "", {"in": ["attrs", "requests"]}),
        (SearchScope.ALL, "requests", "requests"),
        (SearchScope.PROJECT_ONLY, "requests", "requests"),
        (SearchScope.DEPENDENCIES_ONLY, _PROJECT, _PROJECT),
    ],
    ids=["all", "project", "deps", "package", "package-over-project", "package-over-deps"],
)
async def test_search_with_items_maps_scope_and_package_to_one_package_pushdown(
    scope: SearchScope, package: str, pushed: object
) -> None:
    docs = FixedHitsDocs(hits=())
    svc = make_decision_service(records=_EVERY_RECORD, docs=docs)
    await svc.search_with_items("why", scope=scope, package=package)
    assert _pushed_package(docs) == pushed


async def test_the_default_search_is_the_project_corpus() -> None:
    docs = FixedHitsDocs(hits=())
    await make_decision_service(records=_EVERY_RECORD, docs=docs).search_with_items("why")
    assert _pushed_package(docs) == _PROJECT


async def test_scope_deps_without_dependency_records_is_empty_not_the_project() -> None:
    docs = FixedHitsDocs(hits=(decision_chunk(REC_SIDECAR),))
    svc = make_decision_service(records=(REC_SIDECAR, REC_CACHE), docs=docs)
    body, items, extras = await svc.search_with_items("why", scope=SearchScope.DEPENDENCIES_ONLY)
    assert docs.queries == []  # nothing ranked: no fall-through to the project corpus
    no_hits = make_decision_service(records=(), docs=FixedHitsDocs(hits=()))
    assert body == (await no_hits.search_with_items("why"))[0]
    assert "Use SQLite sidecar" not in body
    assert (items, extras) == ((), {"suggestion": SEARCH_ZERO_HIT_SUGGESTION})


# ── hydration across packages + the package tag ─────────────────────────


async def test_a_dependency_hit_hydrates_under_its_own_package_and_is_tagged() -> None:
    docs = FixedHitsDocs(hits=(decision_chunk(REC_POOL),))
    svc = make_decision_service(records=_EVERY_RECORD, docs=docs)
    body, items, _extras = await svc.search_with_items("pool", package="requests")
    assert [(row["id"], row["package"]) for row in items] == [("3", "requests")]
    assert "**Pool connections per host** —" in body
    assert "· from `requests`\n" in body


async def test_scope_deps_answers_every_dependency_with_records() -> None:
    docs = PreFilteringDocs(tuple(decision_chunk(r) for r in _EVERY_RECORD))
    svc = make_decision_service(records=_EVERY_RECORD, docs=docs)
    body, items, _extras = await svc.search_with_items("why", scope=SearchScope.DEPENDENCIES_ONLY)
    assert [row["package"] for row in items] == ["requests", "attrs"]
    assert "Use SQLite sidecar" not in body


async def test_hydration_drops_a_hit_outside_the_requested_packages() -> None:
    """A retrieval branch that ignored the pushdown still cannot put a project
    decision into a dependency answer (or the reverse)."""
    docs = FixedHitsDocs(hits=(decision_chunk(REC_SIDECAR), decision_chunk(REC_POOL)))
    svc = make_decision_service(records=_EVERY_RECORD, docs=docs)
    _body, deps_items, _extras = await svc.search_with_items(
        "why", scope=SearchScope.DEPENDENCIES_ONLY
    )
    _body, why_items, _extras = await svc.why_search("why")
    assert [row["package"] for row in deps_items] == ["requests"]
    assert [row["decision_id"] for row in why_items] == [1]


# ── crowding + byte identity with dependency records present ────────────


def _crowded_ranking() -> tuple[tuple, tuple]:
    """20 dependency decisions ranked above the one project decision."""
    dependency = tuple(
        decision_record(id=100 + i, title=f"Keep rows in shard {i}", package="requests")
        for i in range(20)
    )
    project = decision_record(id=1, title="Keep rows in memory")
    ranked = (*(decision_chunk(r) for r in dependency), decision_chunk(project))
    return (project, *dependency), ranked


async def test_dependency_decisions_do_not_crowd_the_project_out_of_get_why() -> None:
    records, ranked = _crowded_ranking()
    assert len(ranked) > DECISION_PRESET_ROWS  # unscoped, the project hit is ranked out
    mixed = make_decision_service(records=records, docs=PreFilteringDocs(ranked))
    stock = make_decision_service(
        records=records[:1], docs=PreFilteringDocs((decision_chunk(records[0]),))
    )
    body, items, extras = await mixed.why_search("keep rows")
    assert [row["decision_id"] for row in items] == [1]
    assert (body, items, extras) == await stock.why_search("keep rows")
    assert await mixed.search_with_items("keep rows") == await stock.search_with_items("keep rows")


async def test_the_dashboard_is_unchanged_by_dependency_records() -> None:
    stock = make_decision_service(records=(REC_SIDECAR, REC_CACHE))
    mixed = make_decision_service(records=_EVERY_RECORD)
    assert await mixed.why_dashboard() == await stock.why_dashboard()


# ── get_why(targets=...) follows the target's package ───────────────────


def _references(*edges) -> InMemoryReferenceStore:
    references = InMemoryReferenceStore()
    for edge in edges:
        references.by_package.setdefault(edge.from_package, []).append(edge)
    return references


async def test_a_dependency_symbol_surfaces_its_dependencys_decision_tagged() -> None:
    edge = governs_edge(
        key=decision_key(REC_POOL.title), qname="requests.adapters", package="requests"
    )
    svc = make_decision_service(records=_EVERY_RECORD, references=_references(edge))
    body, items, _extras = await svc.why_targets(["requests.adapters"])
    assert [row["decision_id"] for row in items] == [3]
    assert "**Pool connections per host** —" in body
    assert "· from `requests`\n" in body


async def test_colliding_titles_answer_for_the_package_whose_decision_it_is() -> None:
    """A decision key is a normalized title: a dependency decision titled like a
    project one shares its key, and must not answer as the project's (#346)."""
    project = decision_record(id=1, title="Use SQLite sidecar")
    dependency = decision_record(id=2, title="Use SQLite sidecar", package="requests")
    key = decision_key(project.title)
    references = _references(
        governs_edge(key=key, qname="pkg.mod"),
        governs_edge(key=key, qname="requests.mod", package="requests"),
    )
    svc = make_decision_service(records=(project, dependency), references=references)
    dependency_body, dependency_items, _extras = await svc.why_targets(["requests.mod"])
    project_body, project_items, _extras = await svc.why_targets(["pkg.mod"])
    assert [row["decision_id"] for row in dependency_items] == [2]
    assert "from `requests`" in dependency_body
    assert [row["decision_id"] for row in project_items] == [1]
    assert "from `" not in project_body


async def test_a_project_target_answers_the_same_with_dependency_records_present() -> None:
    project_edge = governs_edge(key=decision_key(REC_SIDECAR.title), qname="pkg.mod")
    dependency_edge = governs_edge(
        key=decision_key(REC_POOL.title), qname="requests.adapters", package="requests"
    )
    stock = make_decision_service(
        records=(REC_SIDECAR, REC_CACHE), references=_references(project_edge)
    )
    mixed = make_decision_service(
        records=_EVERY_RECORD, references=_references(project_edge, dependency_edge)
    )
    assert await mixed.why_targets(["pkg.mod"]) == await stock.why_targets(["pkg.mod"])


# ── the internal selector reaches every DecisionNavigator conformer ─────


@pytest.mark.parametrize("conformer", [DecisionService, NullDecisionService])
def test_every_navigator_takes_the_protocol_search_parameters(conformer: type) -> None:
    def shape(method: object) -> list[tuple[str, object, object]]:
        return [(p.name, p.kind, p.default) for p in inspect.signature(method).parameters.values()]

    expected = shape(DecisionNavigator.search_with_items)
    assert shape(conformer.search_with_items) == expected
    # ``branch`` (#313): the branch the decision read answers from.
    names = [name for name, _kind, _default in expected]
    assert names == ["self", "query", "scope", "package", "branch"]
