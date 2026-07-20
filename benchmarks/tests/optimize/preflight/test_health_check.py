"""Dry-run loop health check — every leg exercised + byte-stable rerun (ADR 0018 §2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.candidates.firewall import screen_candidate
from pydocs_eval.optimize.minibatch_filter import FilterDecision
from pydocs_eval.optimize.preflight.health_check import (
    default_rollout_dir,
    run_preflight,
    synthetic_mutation,
)
from pydocs_eval.trajectory.compute_metrics_cli import ComputeMetricsError

_RESOLVED_FIXTURE = Path(__file__).parents[2] / "trajectory/fixtures/run_dir/resolved"


def _rollout() -> Path:
    return _RESOLVED_FIXTURE


def test_default_rollout_dir_locates_fixture() -> None:
    """The offline seam auto-locates the committed widgetlib resolved fixture."""
    assert default_rollout_dir().is_dir()
    assert (default_rollout_dir() / "facts.json").is_file()


def test_synthetic_mutation_is_valid_and_changes_hash() -> None:
    """The synthetic mutation is firewall-valid (ACCEPT path) and shifts the hash."""
    seed = Candidate.seed()
    mutated = synthetic_mutation(seed)
    assert screen_candidate(mutated).valid is True
    assert mutated.candidate_hash != seed.candidate_hash


def test_preflight_is_healthy(tmp_path: Path) -> None:
    """The whole loop runs healthy over the resolved fixture."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    assert result.ok is True
    assert result.validity.valid is True
    assert result.mutated_component == "SERVER_INSTRUCTIONS"


def test_preflight_derives_resolved_record(tmp_path: Path) -> None:
    """Leg 5: the derived record reflects the resolved widgetlib rollout."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    assert result.derived.hard == 1
    assert result.derived.label == "resolved"
    assert result.derived.instance_id == "widgetlib__pricing-discount"


def test_preflight_minibatch_filter_proceeds(tmp_path: Path) -> None:
    """Leg 6: the canned minibatch filter clears its margin and PROCEEDs to the gate."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    assert result.filter_decision is FilterDecision.PROCEED


def test_preflight_gate_consumes_ground_truth(tmp_path: Path) -> None:
    """Leg 7: the simulated gate reports a within-budget full resolve."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    assert result.gate.resolve_rate == 1.0
    assert result.gate.n_graded == 1
    assert result.gate.within_budget is True


def test_preflight_writes_ledger_lineage_entry(tmp_path: Path) -> None:
    """Leg 8: the mutated candidate lands in the super-ledger with seed lineage."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    assert result.ledger_path.is_file()
    assert result.record.lineage_parent == Candidate.seed().candidate_hash
    assert result.record.mutation_record.proposer == "dry-run-synthetic"
    assert result.record.n_rollouts == 1


def test_rerun_ledger_line_is_byte_identical(tmp_path: Path) -> None:
    """A fresh-workspace rerun regenerates a byte-identical ledger line + record hash."""
    a = run_preflight(rollout_fn=_rollout, workspace=tmp_path / "a")
    b = run_preflight(rollout_fn=_rollout, workspace=tmp_path / "b")
    assert a.record.to_line() == b.record.to_line()
    assert a.mutated_hash == b.mutated_hash


def test_ledger_blob_persists_document(tmp_path: Path) -> None:
    """The rendered candidate document is content-addressed beside the ledger."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    blob = result.ledger_path.parent / "blobs" / result.record.document_ref
    assert blob.is_file()


def test_missing_facts_raises(tmp_path: Path) -> None:
    """A rollout dir without facts.json fails loudly (a broken seam, not silent)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises((ComputeMetricsError, FileNotFoundError, OSError, ValueError)):
        run_preflight(rollout_fn=lambda: empty, workspace=tmp_path / "ws")
