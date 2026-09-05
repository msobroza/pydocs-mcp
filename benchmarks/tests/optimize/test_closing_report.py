"""Closing-report skeleton (ADR 0020 §closing report contract): ledger-derived
trajectory counts + pre-registered-question slots left [TO BE MEASURED]."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.optimize.candidates.ledger import (
    CandidateLedger,
    CandidateRecord,
    GateOutcome,
    MutationRecord,
)
from pydocs_eval.optimize.closing_report import (
    render_closing_report_skeleton,
    trajectory_counts,
)


def _valid(name: str, *, gated: bool) -> CandidateRecord:
    gate = (
        GateOutcome(
            resolve_rate=0.5,
            n_graded=10,
            n_infra_excluded=0,
            cost_usd=1.0,
            within_budget=True,
            passed=True,
        )
        if gated
        else None
    )
    return CandidateRecord(
        candidate_hash=name,
        document_ref="ref",
        lineage_parent=None,
        mutation_record=MutationRecord(proposer="p"),
        reflector_input_refs=(),
        valid=True,
        violations=(),
        n_rollouts=1,
        gate=gate,
    )


def _rejected(name: str) -> CandidateRecord:
    return CandidateRecord(
        candidate_hash=name,
        document_ref="ref",
        lineage_parent=None,
        mutation_record=MutationRecord(proposer="p"),
        reflector_input_refs=(),
        valid=False,
        violations=("bad header",),
    )


def _ledger(tmp_path: Path) -> CandidateLedger:
    ledger = CandidateLedger(tmp_path / "candidate-ledger" / "candidates.jsonl")
    for record in (_valid("A", gated=True), _valid("B", gated=False), _rejected("R")):
        ledger.record(record)
    return ledger


def test_trajectory_counts_from_ledger(tmp_path: Path) -> None:
    counts = trajectory_counts(_ledger(tmp_path), accepted_hashes=frozenset({"A"}))
    assert (counts.proposed, counts.valid, counts.validity_rejected) == (3, 2, 1)
    assert counts.gated == 1
    assert counts.accepted == 1


def test_skeleton_lists_trajectory_and_leaves_paid_slots(tmp_path: Path) -> None:
    report = render_closing_report_skeleton(_ledger(tmp_path), accepted_hashes=frozenset({"A"}))
    assert "validity-rejected (zero rollout cost, R3): 1" in report
    assert "accepted (paired-exact rule, ADR 0018): 1" in report
    assert "[TO BE MEASURED]" in report


def test_skeleton_references_pre_registered_questions(tmp_path: Path) -> None:
    report = render_closing_report_skeleton(_ledger(tmp_path))
    assert "ADR 0016" in report and "ADR 0018" in report
    for split in ("### dev", "### val", "### test (seed + one only)"):
        assert split in report


def test_skeleton_default_accepted_is_zero(tmp_path: Path) -> None:
    """Without an accepted set, the skeleton honestly reports zero accepted."""
    report = render_closing_report_skeleton(_ledger(tmp_path))
    assert "accepted (paired-exact rule, ADR 0018): 0" in report
