"""End-to-end SqliteReferenceStore tests (spec §6.2)."""

from __future__ import annotations

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteUnitOfWork


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.fn",
        to_name="other.symbol",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


@pytest.fixture
def provider(tmp_path):
    db = tmp_path / "x.db"
    open_index_database(db).close()
    return PerCallConnectionProvider(cache_path=db)


@pytest.mark.asyncio
async def test_save_many_then_find_callers(provider):
    store = SqliteReferenceStore(provider=provider)
    refs = [
        _ref(from_node_id="pkg.a", to_name="t", to_node_id="t", kind=ReferenceKind.CALLS),
        _ref(
            from_package="other",
            from_node_id="other.x",
            to_name="t",
            to_node_id="t",
            kind=ReferenceKind.CALLS,
        ),
    ]
    await store.save_many(refs, package="pkg")
    callers = await store.find_callers(target_node_id="t")
    assert {r.from_package for r in callers} == {"pkg", "other"}


@pytest.mark.asyncio
async def test_save_many_then_find_callees(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_node_id="pkg.a", to_name="x", to_node_id="x"),
            _ref(from_node_id="pkg.b", to_name="y", to_node_id="y"),
        ],
        package="pkg",
    )
    callees = await store.find_callees(from_node_id="pkg.a")
    assert {r.to_name for r in callees} == {"x"}


@pytest.mark.asyncio
async def test_find_by_name_filter_by_kind(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(to_name="os.path.join", kind=ReferenceKind.CALLS),
            _ref(to_name="os.path.join", kind=ReferenceKind.IMPORTS, from_node_id="pkg.b"),
        ],
        package="pkg",
    )
    all_hits = await store.find_by_name("os.path.join")
    assert len(all_hits) == 2
    calls_only = await store.find_by_name(
        "os.path.join",
        ReferenceKind.CALLS,
    )
    assert {r.kind for r in calls_only} == {ReferenceKind.CALLS}


@pytest.mark.asyncio
async def test_save_many_upsert_on_pk_collision(provider):
    """spec Decision §6.2 — INSERT ON CONFLICT DO UPDATE SET to_node_id.

    Calling save_many twice with the same (from_package, from_node_id,
    to_name, kind) but DIFFERENT to_node_id must update, not crash.
    """
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_ref(to_name="x", to_node_id=None)],
        package="pkg",
    )
    await store.save_many(
        [_ref(to_name="x", to_node_id="pkg.real.x")],
        package="pkg",
    )
    rows = await store.find_by_name("x")
    assert len(rows) == 1
    assert rows[0].to_node_id == "pkg.real.x"


@pytest.mark.asyncio
async def test_delete_for_package_scoped(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _ref(from_package="pkg", to_name="x"),
            _ref(from_package="other", from_node_id="other.x", to_name="y"),
        ],
        package="pkg",
    )
    await store.delete_for_package("pkg")
    rows_x = await store.find_by_name("x")
    rows_y = await store.find_by_name("y")
    assert rows_x == []
    assert len(rows_y) == 1


@pytest.mark.asyncio
async def test_delete_all_wipes_everything(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_ref()], package="pkg")
    await store.delete_all()
    rows = await store.find_by_name("other.symbol")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_zero_refs_is_noop(provider):
    """No-op fast path avoids a useless executemany call."""
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([], package="pkg")
    rows = await store.find_by_name("anything")
    assert rows == []


@pytest.mark.asyncio
async def test_save_many_inside_uow_shares_connection(tmp_path):
    """spec §14.4 — writes inside a SqliteUnitOfWork share the held conn."""
    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)
    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.references.save_many([_ref()], package="pkg")
        await uow.commit()
    # Reopen — row survived.
    store = SqliteReferenceStore(provider=provider)
    rows = await store.find_by_name("other.symbol")
    assert len(rows) == 1


# ── resolve_unresolved (spec C1 — Protocol-level cross-package bulk fixup) ──


@pytest.mark.asyncio
async def test_resolve_unresolved_updates_matching_rows(provider):
    """resolve_unresolved flips ``to_node_id`` for matching unresolved rows."""
    store = SqliteReferenceStore(provider=provider)
    unresolved = _ref(
        from_node_id="pkg.mod.f",
        to_name="other.target",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    await store.save_many([unresolved], package="pkg")

    rows = await store.resolve_unresolved({"other.target"})
    assert rows == 1

    callers = await store.find_by_name("other.target")
    assert len(callers) == 1
    assert callers[0].to_node_id == "other.target"


@pytest.mark.asyncio
async def test_resolve_unresolved_skips_already_resolved(provider):
    """Already-resolved rows are left untouched (idempotent)."""
    store = SqliteReferenceStore(provider=provider)
    resolved = _ref(
        from_node_id="pkg.mod.f",
        to_name="target",
        to_node_id="target",  # already resolved
        kind=ReferenceKind.CALLS,
    )
    await store.save_many([resolved], package="pkg")
    rows = await store.resolve_unresolved({"target"})
    assert rows == 0


@pytest.mark.asyncio
async def test_resolve_unresolved_empty_set_is_noop(provider):
    """Empty qname set → 0 rows without touching the DB."""
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_ref(to_name="x", to_node_id=None)], package="pkg")
    rows = await store.resolve_unresolved(set())
    assert rows == 0


# ── find_transitive_callers (blast-radius reverse walk, lookup(show="impact")) ──


def _call(frm: str, to: str, kind: ReferenceKind = ReferenceKind.CALLS) -> NodeReference:
    """A resolved caller edge ``frm`` → ``to`` (frm calls to)."""
    return _ref(from_node_id=frm, to_name=to, to_node_id=to, kind=kind)


@pytest.mark.asyncio
async def test_transitive_callers_single_hop(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_call("A", "T")], package="pkg")
    rows = await store.find_transitive_callers("T", max_depth=1)
    assert [(q, hop) for q, hop, _ in rows] == [("A", 1)]


@pytest.mark.asyncio
async def test_transitive_callers_multi_hop_within_depth(provider):
    # C -> B -> A -> T  (each calls the next)
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_call("A", "T"), _call("B", "A"), _call("C", "B")], package="pkg")
    rows = await store.find_transitive_callers("T", max_depth=2)
    got = {q: hop for q, hop, _ in rows}
    assert got == {"A": 1, "B": 2}  # C is at hop 3 → beyond max_depth=2


@pytest.mark.asyncio
async def test_transitive_callers_min_hop_dedup(provider):
    # A -> T (direct), A -> B, B -> T : A reachable at hop 1 (direct) and hop 2 (via B).
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_call("A", "T"), _call("B", "T"), _call("A", "B")], package="pkg")
    rows = await store.find_transitive_callers("T", max_depth=3)
    got = {q: hop for q, hop, _ in rows}
    assert got == {"A": 1, "B": 1}  # A reported once at its MIN hop


@pytest.mark.asyncio
async def test_transitive_callers_cycle_terminates_and_excludes_target(provider):
    # A -> T and T -> A (cycle back through the target).
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_call("A", "T"), _call("T", "A")], package="pkg")
    rows = await store.find_transitive_callers("T", max_depth=5)
    assert [q for q, _, _ in rows] == ["A"]  # terminates; target never lists itself


@pytest.mark.asyncio
async def test_transitive_callers_depth_bound(provider):
    # D -> C -> B -> A -> T ; depth 2 keeps only A (1) and B (2).
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_call("A", "T"), _call("B", "A"), _call("C", "B"), _call("D", "C")], package="pkg"
    )
    rows = await store.find_transitive_callers("T", max_depth=2)
    assert {q for q, _, _ in rows} == {"A", "B"}


@pytest.mark.asyncio
async def test_transitive_callers_skips_unresolved_edges(provider):
    # X "calls" T by NAME but the edge is unresolved (to_node_id=None) → not a caller.
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_call("A", "T"), _ref(from_node_id="X", to_name="T", to_node_id=None)], package="pkg"
    )
    rows = await store.find_transitive_callers("T", max_depth=2)
    assert {q for q, _, _ in rows} == {"A"}


@pytest.mark.asyncio
async def test_transitive_callers_excludes_similar_edges(provider):
    # A 'similar' edge into T must not count as a caller.
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [_call("A", "T"), _call("S", "T", kind=ReferenceKind.SIMILAR)], package="pkg"
    )
    rows = await store.find_transitive_callers("T", max_depth=2)
    assert {q for q, _, _ in rows} == {"A"}


@pytest.mark.asyncio
async def test_transitive_callers_in_degree_counts_real_callers(provider):
    # A -> T ; B -> A, C -> A (2 real callers of A), plus a 'similar' edge into A.
    store = SqliteReferenceStore(provider=provider)
    await store.save_many(
        [
            _call("A", "T"),
            _call("B", "A"),
            _call("C", "A"),
            _call("S", "A", kind=ReferenceKind.SIMILAR),
        ],
        package="pkg",
    )
    rows = await store.find_transitive_callers("T", max_depth=1)
    in_deg = {q: deg for q, _, deg in rows}
    assert in_deg["A"] == 2  # B + C ; 'similar' excluded


@pytest.mark.asyncio
async def test_transitive_callers_empty_when_no_callers(provider):
    store = SqliteReferenceStore(provider=provider)
    await store.save_many([_call("A", "B")], package="pkg")
    rows = await store.find_transitive_callers("T", max_depth=3)
    assert rows == []
