"""Decisions summary (§D17 block 8) — value object, service population, render.

Four concerns:
1. ``OverviewCard`` gains ``decisions_summary: DecisionsBlock | None`` — counts by
   status + the stalest ACTIVE record's title and staleness band.
2. ``OverviewService.build`` populates it from ``uow.decisions.list_for_package``
   when decision rows exist, and leaves it ``None`` (block silently omitted) when
   capture is disabled / no records were mined — the aggregate-view omit rule
   (unlike ``get_why`` which raises).
3. Renderer golden — ``## Decisions`` with the ``[[next:why:]]`` pointer.
4. The ``why`` pointer action resolves per surface (``get_why(...)`` / ``pydocs-mcp
   why``) — the grammar extension this task adds.
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import (
    format_overview_card,
    pointer_token,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.application.overview_service import (
    DecisionsBlock,
    OverviewCard,
    OverviewService,
)
from pydocs_mcp.storage.decision_record import DecisionRecord
from tests._fakes import InMemoryDecisionStore, make_fake_uow_factory

_PKG = "__project__"


def _record(
    *,
    id: int,
    title: str,
    status: str = "active",
    staleness_score: float = 0.1,
) -> DecisionRecord:
    return DecisionRecord(
        id=id,
        package=_PKG,
        title=title,
        status=status,
        source="commit_messages",
        confidence=0.9,
        evidence=(),
        affected_files=(),
        affected_qnames=(),
        staleness_score=staleness_score,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=0.0,
        updated_at=0.0,
    )


def _card_with_decisions(block: DecisionsBlock | None) -> OverviewCard:
    return OverviewCard(
        package=_PKG,
        package_count=1,
        module_count=1,
        symbol_count=1,
        doc_coverage=1.0,
        modules=(),
        entry_points=(),
        communities=(),
        dependency_profile=(),
        node_scores_available=True,
        decisions_summary=block,
    )


# ── 1. Renderer golden ───────────────────────────────────────────────────


def test_decisions_block_rendered_with_pointer() -> None:
    block = DecisionsBlock(
        by_status={"active": 3, "proposed": 1},
        stalest_title="Use SQLite sidecar",
        stalest_score=0.7,
    )
    out = format_overview_card(_card_with_decisions(block))
    assert "## Decisions" in out
    assert "active: 3" in out and "proposed: 1" in out
    # The stalest ACTIVE record is surfaced with its band.
    assert "Use SQLite sidecar" in out and "stale" in out
    # A next-step pointer into the full get_why surface.
    assert "[[next:why:]]" in out


def test_decisions_block_omitted_when_none() -> None:
    out = format_overview_card(_card_with_decisions(None))
    assert "## Decisions" not in out
    assert "[[next:why:]]" not in out


def test_decisions_block_without_stalest_active() -> None:
    # Records exist but none is active → no stalest line, block still renders
    # the status census.
    block = DecisionsBlock(
        by_status={"proposed": 2},
        stalest_title=None,
        stalest_score=None,
    )
    out = format_overview_card(_card_with_decisions(block))
    assert "## Decisions" in out
    assert "proposed: 2" in out


# ── 2. `why` pointer grammar extension ───────────────────────────────────


def test_why_pointer_token_and_resolution() -> None:
    assert pointer_token("why", "") == "[[next:why:]]"
    assert resolve_pointers("[[next:why:]]", "mcp") == "→ get_why()"
    assert resolve_pointers("[[next:why:]]", "cli") == "→ pydocs-mcp why"


def test_why_pointer_with_query_target() -> None:
    assert pointer_token("why", "sidecar") == "[[next:why:sidecar]]"
    assert resolve_pointers("[[next:why:sidecar]]", "mcp") == '→ get_why(query="sidecar")'
    assert resolve_pointers("[[next:why:sidecar]]", "cli") == '→ pydocs-mcp why "sidecar"'


def test_strip_removes_why_token() -> None:
    with_token = "## Decisions\nbody\n[[next:why:]]\n"
    assert strip_pointers(with_token) == "## Decisions\nbody\n"


# ── 3. Service population from uow.decisions ──────────────────────────────


async def test_build_populates_decisions_from_store() -> None:
    store = InMemoryDecisionStore()
    await store.upsert(
        (
            _record(id=1, title="Use SQLite sidecar", status="active", staleness_score=0.7),
            _record(id=2, title="Adopt gRPC", status="proposed", staleness_score=0.9),
            _record(id=3, title="Cache chunks", status="active", staleness_score=0.2),
        )
    )
    svc = OverviewService(
        uow_factory=make_fake_uow_factory(decisions=store),
        scripts={},
    )
    card = await svc.build()
    block = card.decisions_summary
    assert block is not None
    assert block.by_status == {"active": 2, "proposed": 1}
    # Stalest ACTIVE (0.7) — the proposed 0.9 is not "active" so it is excluded.
    assert block.stalest_title == "Use SQLite sidecar"
    assert block.stalest_score == 0.7
    assert "## Decisions" in format_overview_card(card)


async def test_build_decisions_none_when_no_records() -> None:
    # Capture disabled / nothing mined → empty store → block omitted (None),
    # the aggregate-view silent-omit rule (get_why raises; the overview does not).
    svc = OverviewService(uow_factory=make_fake_uow_factory(), scripts={})
    card = await svc.build()
    assert card.decisions_summary is None
    assert "## Decisions" not in format_overview_card(card)


async def test_build_decisions_no_active_records() -> None:
    store = InMemoryDecisionStore()
    await store.upsert((_record(id=1, title="Rejected idea", status="rejected"),))
    svc = OverviewService(uow_factory=make_fake_uow_factory(decisions=store), scripts={})
    card = await svc.build()
    block = card.decisions_summary
    assert block is not None
    assert block.by_status == {"rejected": 1}
    assert block.stalest_title is None and block.stalest_score is None
