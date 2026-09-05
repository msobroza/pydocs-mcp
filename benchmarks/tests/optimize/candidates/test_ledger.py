"""Candidate ledger — lineage schema, golden bytes, zero-rollout invariant (R3)."""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.candidates.ledger import (
    CandidateLedger,
    CandidateRecord,
    GateOutcome,
    MutationRecord,
)

_GOLDEN_RECORD = CandidateRecord(
    candidate_hash="a" * 64,
    document_ref="b" * 64,
    lineage_parent="c" * 64,
    mutation_record=MutationRecord(
        proposer="gepa_reflection",
        component="TOOL: grep",
        selector="round_robin",
        metadata={"iteration": "3", "model": "sonnet-5"},
    ),
    reflector_input_refs=("d" * 64, "e" * 64),
    valid=True,
    violations=(),
    n_rollouts=3,
    minibatch_scores={"soft": 0.5, "hard": 0.0},
    gate=GateOutcome(
        resolve_rate=0.25,
        n_graded=4,
        n_infra_excluded=1,
        cost_usd=1.5,
        within_budget=True,
        passed=True,
    ),
    campaign_ids=("campaign-1", "campaign-2"),
)

# Byte-for-byte canonical JSON line (sorted keys, no spaces). Pins the on-disk
# schema so a field rename / reorder / type change fails loudly (ledger-idiom).
_GOLDEN_LINE = (
    '{"campaign_ids":["campaign-1","campaign-2"],'
    '"candidate_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"document_ref":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
    '"gate":{"cost_usd":1.5,"n_graded":4,"n_infra_excluded":1,"passed":true,'
    '"resolve_rate":0.25,"within_budget":true},'
    '"lineage_parent":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
    '"minibatch_scores":{"hard":0.0,"soft":0.5},'
    '"mutation_record":{"component":"TOOL: grep","metadata":{"iteration":"3","model":"sonnet-5"},'
    '"proposer":"gepa_reflection","selector":"round_robin"},'
    '"n_rollouts":3,'
    '"reflector_input_refs":["dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
    '"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"],'
    '"valid":true,"violations":[]}'
)


def _rejected(candidate_hash: str = "f" * 64) -> CandidateRecord:
    return CandidateRecord(
        candidate_hash=candidate_hash,
        document_ref="0" * 64,
        lineage_parent="c" * 64,
        mutation_record=MutationRecord(proposer="synthetic", component="TOOL: grep"),
        reflector_input_refs=(),
        valid=False,
        violations=("'TOOL: grep' missing §D13 marker 'Examples'",),
    )


def test_golden_line_is_byte_stable() -> None:
    assert _GOLDEN_RECORD.to_line() == _GOLDEN_LINE


def test_record_round_trips_through_json() -> None:
    import json

    restored = CandidateRecord.from_record(json.loads(_GOLDEN_RECORD.to_line()))
    assert restored == _GOLDEN_RECORD


def test_append_then_latest_resumes(tmp_path) -> None:
    ledger = CandidateLedger(path=tmp_path / "candidates.jsonl")
    ledger.record(_GOLDEN_RECORD)
    reloaded = CandidateLedger(path=tmp_path / "candidates.jsonl")
    assert reloaded.latest("a" * 64) == _GOLDEN_RECORD
    assert reloaded.latest("missing") is None


def test_append_is_additive_and_last_write_wins(tmp_path) -> None:
    path = tmp_path / "candidates.jsonl"
    ledger = CandidateLedger(path=path)
    ledger.record(_GOLDEN_RECORD)
    updated = CandidateRecord.from_record({**_GOLDEN_RECORD.to_record(), "n_rollouts": 5})
    ledger.record(updated)
    # Two physical lines (append-only), one index entry (last write wins).
    assert len(path.read_text().splitlines()) == 2
    assert ledger.latest("a" * 64).n_rollouts == 5


def test_invalid_candidate_shows_zero_rollouts(tmp_path) -> None:
    # R3 demonstrable: a validity-rejected candidate's ledger entry proves it
    # never spent a rollout — straight from the ledger, no external bookkeeping.
    ledger = CandidateLedger(path=tmp_path / "candidates.jsonl")
    ledger.record(_rejected())
    entry = ledger.latest("f" * 64)
    assert entry.valid is False
    assert entry.n_rollouts == 0
    assert entry.minibatch_scores == {} and entry.gate is None
    assert ledger.total_rollouts() == 0


def test_invalid_candidate_claiming_a_rollout_is_a_construction_error() -> None:
    # The zero-rollout guarantee is structural, not conventional.
    with pytest.raises(ValueError, match="invalid candidate claims rollout spend"):
        CandidateRecord(
            candidate_hash="f" * 64,
            document_ref="0" * 64,
            lineage_parent=None,
            mutation_record=MutationRecord(proposer="synthetic"),
            reflector_input_refs=(),
            valid=False,
            violations=("bad",),
            n_rollouts=2,  # <-- an invalid candidate cannot have spent a rollout
        )


def test_total_rollouts_accrual_is_idempotent(tmp_path) -> None:
    # An exact-duplicate append must not double-count rollouts (campaign-ledger
    # spend_key idiom); a genuinely new line for the same candidate does count.
    ledger = CandidateLedger(path=tmp_path / "candidates.jsonl")
    ledger.record(_GOLDEN_RECORD)
    ledger.record(_GOLDEN_RECORD)  # exact duplicate
    assert ledger.total_rollouts() == 3
    updated = CandidateRecord.from_record({**_GOLDEN_RECORD.to_record(), "n_rollouts": 4})
    ledger.record(updated)  # distinct line -> accrues additionally
    assert ledger.total_rollouts() == 7


def test_corrupt_trailing_line_is_skipped(tmp_path) -> None:
    path = tmp_path / "candidates.jsonl"
    ledger = CandidateLedger(path=path)
    ledger.record(_GOLDEN_RECORD)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"candidate_hash": "truncated"\n')  # killed mid-write
    reloaded = CandidateLedger(path=path)
    assert reloaded.latest("a" * 64) == _GOLDEN_RECORD  # the good line survives


def test_stage_document_and_reflector_inputs_are_content_addressed(tmp_path) -> None:
    ledger = CandidateLedger(path=tmp_path / "candidates.jsonl")
    ref = ledger.stage_document("=== SERVER_INSTRUCTIONS ===\nhi\n")
    assert len(ref) == 64
    assert (ledger.blobs_dir / ref).read_text(
        encoding="utf-8"
    ) == "=== SERVER_INSTRUCTIONS ===\nhi\n"
    refs = ledger.stage_reflector_inputs([b"fact one", b"fact two"])
    assert len(refs) == 2 and all(len(r) == 64 for r in refs)
    # Identical bytes dedupe to one blob (write-once by hash).
    assert ledger.stage_blob(b"fact one") == refs[0]
