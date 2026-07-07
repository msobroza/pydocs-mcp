"""Tests for the pure merge / staleness / reconcile engine (spec §D8-§D10).

All functions under test are pure: ``merge_raw_decisions`` collapses per-source
:class:`RawDecision`\\s on token-Jaccard title similarity (evidence accretion,
confidence never lowered by corroboration); ``staleness_score`` scores a
decision's freshness from affected-file mtimes + evidence age; ``reconcile``
matches merged incoming decisions against persisted :class:`DecisionRecord`\\s
by normalized title, preserving ``id`` / ``created_at`` / ``superseded_by`` and
bumping ``updated_at`` only on evidence-content change.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.decisions.engine import (
    merge_raw_decisions,
    reconcile,
    staleness_score,
)
from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord

# A fixed "now" so age terms are deterministic (seconds since epoch).
NOW = 1_700_000_000.0

EV_A = DecisionEvidence(source="inline_markers", locator="pkg/mod.py:10-30", text="alpha")
EV_B = DecisionEvidence(source="commit_messages", locator="aaaa1111", text="beta")


def _raw(
    *,
    title: str = "Use sidecar for vectors",
    status: str = "active",
    source: str = "inline_markers",
    confidence: float = 0.95,
    evidence: tuple[DecisionEvidence, ...] = (EV_A,),
    affected_files: tuple[str, ...] = ("pkg/mod.py",),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
    evidence_date: float | None = None,
) -> RawDecision:
    return RawDecision(
        title=title,
        status=status,
        source=source,
        confidence=confidence,
        evidence=evidence,
        affected_files=affected_files,
        affected_qnames=affected_qnames,
        evidence_date=evidence_date,
    )


def _record(
    *,
    id: int | None = None,
    title: str = "Use sidecar for vectors",
    status: str = "active",
    source: str = "inline_markers",
    confidence: float = 0.95,
    evidence: tuple[DecisionEvidence, ...] = (EV_A,),
    affected_files: tuple[str, ...] = ("pkg/mod.py",),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
    staleness_score: float = 0.0,
    superseded_by: int | None = None,
    verification: str = "verbatim",
    structured=None,
    created_at: float = 100.0,
    updated_at: float = 100.0,
) -> DecisionRecord:
    return DecisionRecord(
        id=id,
        package="__project__",
        title=title,
        status=status,
        source=source,
        confidence=confidence,
        evidence=evidence,
        affected_files=affected_files,
        affected_qnames=affected_qnames,
        staleness_score=staleness_score,
        superseded_by=superseded_by,
        verification=verification,
        structured=structured,
        created_at=created_at,
        updated_at=updated_at,
    )


def _merged(
    *,
    title: str = "Use sidecar for vectors",
    evidence: tuple[DecisionEvidence, ...] = (EV_A,),
    confidence: float = 0.95,
    status: str = "active",
    source: str = "inline_markers",
    affected_files: tuple[str, ...] = ("pkg/mod.py",),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
    evidence_date: float | None = None,
) -> RawDecision:
    """A merge-output RawDecision (what ``reconcile`` consumes as incoming)."""
    return _raw(
        title=title,
        status=status,
        source=source,
        confidence=confidence,
        evidence=evidence,
        affected_files=affected_files,
        affected_qnames=affected_qnames,
        evidence_date=evidence_date,
    )


# ── merge_raw_decisions ─────────────────────────────────────────────────────


def test_same_title_merges_evidence_and_raises_confidence() -> None:
    a = _raw(title="Use sidecar for vectors", source="inline_markers", confidence=0.95)
    b = _raw(
        title="use sidecar for vectors!",
        source="commit_messages",
        confidence=0.70,
        evidence=(EV_B,),
    )
    merged = merge_raw_decisions((a, b), jaccard_threshold=0.85)
    assert len(merged) == 1
    m = merged[0]
    assert len(m.evidence) == 2
    assert m.confidence == min(1.0, 0.95 + 0.05)  # max + 0.05/corroborator, capped 1.0
    assert m.source == "inline_markers"  # primary = highest-confidence source


def test_adr_confidence_not_lowered_by_corroboration() -> None:
    merged = merge_raw_decisions(
        (_raw(confidence=1.0, source="adr_files"), _raw(confidence=0.70, evidence=(EV_B,))),
        jaccard_threshold=0.85,
    )
    assert merged[0].confidence == 1.0


def test_distinct_titles_do_not_merge() -> None:
    a = _raw(title="Use sidecar for vectors")
    b = _raw(title="Adopt async pipeline for retrieval")
    merged = merge_raw_decisions((a, b), jaccard_threshold=0.85)
    assert len(merged) == 2


# ── staleness_score ─────────────────────────────────────────────────────────


def test_staleness_age_only_when_no_affected_files(tmp_path) -> None:
    s = staleness_score(
        affected_files=(),
        updated_at=NOW - 400 * 86400.0,
        now=NOW,
        root=tmp_path,
    )
    assert s == pytest.approx(0.3 * 1.0)  # changed_ratio := 0, age capped at 1y


def test_staleness_weights_changed_files(tmp_path) -> None:
    # Two affected files; one touched AFTER updated_at, one before.
    updated_at = NOW - 10 * 86400.0
    fresh = tmp_path / "fresh.py"
    stale = tmp_path / "stale.py"
    fresh.write_text("x = 1\n")
    stale.write_text("y = 2\n")
    import os

    # fresh.py mtime > updated_at (changed since the decision), stale.py older.
    os.utime(fresh, (NOW, NOW))
    os.utime(stale, (updated_at - 86400.0, updated_at - 86400.0))
    age_years = (NOW - updated_at) / (365 * 86400.0)
    s = staleness_score(
        affected_files=("fresh.py", "stale.py"),
        updated_at=updated_at,
        now=NOW,
        root=tmp_path,
    )
    assert s == pytest.approx(0.7 * 0.5 + 0.3 * min(1.0, age_years))


# ── reconcile ───────────────────────────────────────────────────────────────


def test_reconcile_preserves_id_created_at_supersession() -> None:
    existing = _record(id=7, created_at=100.0, superseded_by=3, evidence=(EV_A,))
    incoming = _merged(title=existing.title, evidence=(EV_A, EV_B))
    out = reconcile(existing=(existing,), incoming=(incoming,), now=500.0)
    kept = out.upserts[0]
    assert kept.id == 7 and kept.created_at == 100.0 and kept.superseded_by == 3
    # evidence changed → bump updated_at
    assert len(kept.evidence) == 2 and kept.updated_at != existing.updated_at


def test_reconcile_no_evidence_change_keeps_updated_at() -> None:
    existing = _record(id=7, created_at=100.0, updated_at=100.0, evidence=(EV_A,))
    incoming = _merged(title=existing.title, evidence=(EV_A,))
    out = reconcile(existing=(existing,), incoming=(incoming,), now=500.0)
    kept = out.upserts[0]
    assert kept.id == 7 and kept.updated_at == 100.0  # evidence unchanged → no bump


def test_reconcile_deletes_vanished() -> None:
    out = reconcile(existing=(_record(id=9, title="gone"),), incoming=(), now=500.0)
    assert out.delete_ids == (9,)


def test_reconcile_new_incoming_becomes_record() -> None:
    incoming = _merged(title="brand new decision", evidence=(EV_A,), evidence_date=200.0)
    out = reconcile(existing=(), incoming=(incoming,), now=500.0)
    assert len(out.upserts) == 1 and out.delete_ids == ()
    new = out.upserts[0]
    # new record: id None, created_at == updated_at == evidence_date (or now)
    assert new.id is None and new.created_at == 200.0 and new.updated_at == 200.0
