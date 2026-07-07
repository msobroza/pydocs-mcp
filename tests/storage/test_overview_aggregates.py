"""End-to-end tests for the overview storage aggregates (spec §D17).

Seeds a real SQLite db (mirroring ``tests/storage/test_reference_store.py``
conventions), inserts ``node_references`` / ``node_scores`` rows directly,
and exercises the read-side aggregates: ``degree_by_package`` /
``imports_grouped_by_target`` (ReferenceStore) and ``for_package`` /
``community_cohesion`` (NodeScoreStore).
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.node_score import CommunityCohesion
from pydocs_mcp.storage.sqlite import (
    SqliteNodeScoreRepository,
    SqliteReferenceStore,
)


@pytest.fixture
def provider(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    return PerCallConnectionProvider(cache_path=db)


def _insert_refs(provider, rows) -> None:
    """rows: (from_package, from_node_id, to_name, to_node_id, kind)."""
    conn = open_index_database(provider.cache_path)
    conn.executemany(
        "INSERT INTO node_references "
        "(from_package, from_node_id, to_name, to_node_id, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _insert_scores(provider, rows) -> None:
    """rows: (package, qualified_name, in_degree, pagerank, community)."""
    conn = open_index_database(provider.cache_path)
    conn.executemany(
        "INSERT INTO node_scores "
        "(package, qualified_name, in_degree, pagerank, community) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_degree_by_package_counts_in_and_out(provider) -> None:
    # rows: a->b CALLS, c->b CALLS, b->d CALLS  (all package "__project__")
    _insert_refs(
        provider,
        [
            ("__project__", "a", "b", "b", "calls"),
            ("__project__", "c", "b", "b", "calls"),
            ("__project__", "b", "d", "d", "calls"),
        ],
    )
    store = SqliteReferenceStore(provider=provider)
    degrees = asyncio.run(store.degree_by_package("__project__"))
    assert degrees["b"] == (2, 1)  # (in_degree, out_degree)
    assert degrees["a"] == (0, 1)


def test_degree_by_package_excludes_synthetic_decision_nodes(provider) -> None:
    """A ``decision:<key>`` GOVERNS source is a synthetic graph node, not a
    resolved qname — it must NOT enter the centrality map, or it leaks into the
    overview's entry-points (in_deg 0, out_deg > 0 reads as a graph root) and the
    dashboard's ungoverned-modules list (spec §D17/§D18)."""
    _insert_refs(
        provider,
        [
            # A real CALLS edge so the qname side is populated normally.
            ("__project__", "app.main", "app.greet", "app.greet", "calls"),
            # The synthetic GOVERNS edge Task-9's emit_governs_edges stamps.
            ("__project__", "decision:greeting-stays-pure", "app", "app", "governs"),
        ],
    )
    store = SqliteReferenceStore(provider=provider)
    degrees = asyncio.run(store.degree_by_package("__project__"))
    assert "decision:greeting-stays-pure" not in degrees
    # The real qnames still count (governed target keeps its inbound edge).
    assert degrees["app"][0] == 1  # in_degree from the GOVERNS edge
    assert degrees["app.main"][1] == 1  # out_degree from the CALLS edge


def test_imports_grouped_by_target_package(provider) -> None:
    # IMPORTS edges: x->numpy.array, y->numpy.linalg.solve, z->pydantic.BaseModel
    _insert_refs(
        provider,
        [
            ("__project__", "x", "numpy.array", "numpy.array", "imports"),
            ("__project__", "y", "numpy.linalg.solve", "numpy.linalg.solve", "imports"),
            ("__project__", "z", "pydantic.BaseModel", "pydantic.BaseModel", "imports"),
        ],
    )
    store = SqliteReferenceStore(provider=provider)
    profile = asyncio.run(store.imports_grouped_by_target("__project__"))
    assert profile == {"numpy": 2, "pydantic": 1}


def test_community_cohesion(provider) -> None:
    # node_scores: a,b community 1; c community 2. references: a->b (intra), a->c (cross)
    _insert_scores(
        provider,
        [
            ("__project__", "a", 0, 0.0, 1),
            ("__project__", "b", 0, 0.0, 1),
            ("__project__", "c", 0, 0.0, 2),
        ],
    )
    _insert_refs(
        provider,
        [
            ("__project__", "a", "b", "b", "calls"),
            ("__project__", "a", "c", "c", "calls"),
        ],
    )
    score_store = SqliteNodeScoreRepository(provider=provider)
    cohesion = asyncio.run(score_store.community_cohesion("__project__"))
    assert cohesion[1].size == 2
    assert cohesion[1].intra_edges == 1 and cohesion[1].cross_edges == 1
    assert isinstance(cohesion[1], CommunityCohesion)


def test_scores_for_package_returns_all_rows(provider) -> None:
    _insert_scores(
        provider,
        [
            ("__project__", "a", 1, 0.1, 1),
            ("__project__", "b", 2, 0.2, 1),
            ("__project__", "c", 3, 0.3, 2),
        ],
    )
    score_store = SqliteNodeScoreRepository(provider=provider)
    rows = asyncio.run(score_store.for_package("__project__"))
    assert {r.qualified_name for r in rows} == {"a", "b", "c"}
