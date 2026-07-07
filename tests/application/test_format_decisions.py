"""Golden layout for the decision renderers (spec §D5/§D10/§D11).

Pure rendering tests: build ``DecisionRecord`` / ``DecisionDashboard`` fixtures
and assert the record-block layout (bold title, status/confidence/staleness-band
line, verbatim evidence citations, affected-qname pointers, superseded link,
unverified caveat, structured rationale/alternatives sections) plus the
governance dashboard sections. No I/O — these exercise ``formatting.py`` alone.
"""

from __future__ import annotations

from pydocs_mcp.application.decision_service import DecisionDashboard
from pydocs_mcp.application.formatting import (
    _staleness_band,
    format_decision_dashboard,
    format_decision_records,
)
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord


def _record(
    *,
    id: int | None = 1,
    title: str = "Use SQLite sidecar",
    status: str = "active",
    confidence: float = 0.95,
    staleness_score: float = 0.1,
    superseded_by: int | None = None,
    verification: str = "verbatim",
    structured=None,
    affected_files: tuple[str, ...] = (),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
    evidence: tuple[DecisionEvidence, ...] = (
        DecisionEvidence(source="commit_messages", locator="pkg/mod.py:10-30", text="use sqlite"),
    ),
) -> DecisionRecord:
    return DecisionRecord(
        id=id,
        package="__project__",
        title=title,
        status=status,
        source="commit_messages",
        confidence=confidence,
        evidence=evidence,
        affected_files=affected_files,
        affected_qnames=affected_qnames,
        staleness_score=staleness_score,
        superseded_by=superseded_by,
        verification=verification,
        structured=structured,
        created_at=0.0,
        updated_at=0.0,
    )


def test_record_block_layout() -> None:
    out = format_decision_records(
        (_record(staleness_score=0.1),), heading="Decisions matching 'sidecar'"
    )
    assert out.startswith("# Decisions matching 'sidecar'\n")
    assert "**Use SQLite sidecar** — active · confidence 0.95 · fresh" in out
    assert "pkg/mod.py:10-30" in out  # evidence citation rendered
    assert "[[next:lookup:pkg.mod]]" in out  # affected-qname pointer (§D5)
    assert out.endswith("\n")


def test_staleness_bands() -> None:
    assert _staleness_band(0.1) == "fresh"
    assert _staleness_band(0.4) == "drifting"
    assert _staleness_band(0.7) == "stale"
    # Boundary: <0.3 fresh, 0.3–0.5 drifting, >0.5 stale (spec §D10).
    assert _staleness_band(0.3) == "drifting"
    assert _staleness_band(0.5) == "drifting"


def test_superseded_link_and_unverified_caveat() -> None:
    rec = _record(status="superseded", superseded_by=42, verification="unverified")
    out = format_decision_records((rec,), heading="Decisions")
    assert "superseded by #42" in out
    assert "unverified" in out  # LLM-structured, not evidence-grounded caveat


def test_structured_fields_rendered_when_present() -> None:
    rec = _record(
        structured={
            "rationale": "SQLite ships with Python; zero-config sidecar.",
            "alternatives": "Postgres (needs a server), flat files (no query).",
        }
    )
    out = format_decision_records((rec,), heading="Decisions")
    assert "SQLite ships with Python" in out
    assert "Postgres (needs a server)" in out


def test_affected_qname_pointers_capped_at_three() -> None:
    rec = _record(affected_qnames=("a.b", "c.d", "e.f", "g.h", "i.j"))
    out = format_decision_records((rec,), heading="Decisions")
    # One pointer per qname, capped at 3 (§D5 — don't flood the card).
    assert out.count("[[next:lookup:") == 3
    assert "[[next:lookup:a.b]]" in out
    assert "[[next:lookup:g.h]]" not in out


def _summary() -> DecisionDashboard:
    return DecisionDashboard(
        by_status={"active": 3, "proposed": 1, "superseded": 1},
        by_source={"commit_messages": 4, "adr_files": 1},
        stalest=(_record(title="Use SQLite sidecar", staleness_score=0.8),),
        awaiting_review=(_record(title="Adopt gRPC", status="proposed", staleness_score=0.2),),
        ungoverned_modules=("pkg.core", "pkg.api"),
    )


def test_dashboard_layout() -> None:
    out = format_decision_dashboard(_summary())
    assert "## By status" in out and "## Stalest active" in out and "## Awaiting review" in out
    assert "## Ungoverned high-centrality modules" in out
    assert "## By source" in out
    assert "active: 3" in out
    assert "Use SQLite sidecar" in out
    assert "Adopt gRPC" in out
    assert "`pkg.core`" in out
    assert out.endswith("\n")


def test_dashboard_no_internal_jargon() -> None:
    out = format_decision_dashboard(_summary())
    for bad in ("sub-PR", "PR #", "RRF", "FTS5", "TurboQuant", "trilogy"):
        assert bad not in out
