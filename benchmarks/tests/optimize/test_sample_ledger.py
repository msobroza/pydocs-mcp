"""Sample-level rubric ledger — per-sample resume sidecar (spec AC-11, AC-12)."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.optimize.rubric.model import SampleRubricRecord
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger


def _record(
    *,
    fingerprint: str = "f" * 64,
    split: str = "train",
    task_id: str = "t1",
    objective_hash: str = "o" * 64,
    verdict: float = 0.55,
    cost_usd: float = 0.31,
    discarded: str | None = None,
) -> SampleRubricRecord:
    return SampleRubricRecord(
        fingerprint=fingerprint,
        split=split,
        task_id=task_id,
        qa_type="how",
        objective_hash=objective_hash,
        gates={"non_empty": True, "grounded": False},
        gate_pass_fraction=0.5,
        judge_skipped=False,
        criteria={"correctness": 7.0},
        rubric_score=0.7,
        verdict=verdict,
        turns=6,
        wall_seconds=41.2,
        cost_usd=cost_usd,
        answer_sha256="a" * 64,
        discarded=discarded,
    )


def test_record_and_lookup_roundtrip(tmp_path: Path) -> None:
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record())
    hit = ledger.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
    assert hit is not None and hit.verdict == 0.55
    assert hit.gates == {"non_empty": True, "grounded": False}


def test_key_components_never_collide(tmp_path: Path) -> None:
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record())
    assert (
        ledger.lookup(fingerprint="f" * 64, split="holdout", task_id="t1", objective_hash="o" * 64)
        is None
    )
    assert (
        ledger.lookup(fingerprint="f" * 64, split="train", task_id="t2", objective_hash="o" * 64)
        is None
    )


def test_different_objective_hash_is_a_miss(tmp_path: Path) -> None:
    # AC-12: the same candidate under a different rubric never falsely resumes.
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record())
    assert (
        ledger.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="x" * 64)
        is None
    )


def test_resume_reads_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    SampleRubricLedger(path).record(_record())
    reloaded = SampleRubricLedger(path)
    assert (
        reloaded.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
        is not None
    )


def test_append_only_two_records_two_lines(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    ledger = SampleRubricLedger(path)
    ledger.record(_record(task_id="t1"))
    ledger.record(_record(task_id="t2"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_corrupt_line_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    ledger = SampleRubricLedger(path)
    ledger.record(_record())
    path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    reloaded = SampleRubricLedger(path)
    assert (
        reloaded.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
        is not None
    )


def test_total_spend_sums_costs(tmp_path: Path) -> None:
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record(task_id="t1", cost_usd=0.25))
    ledger.record(_record(task_id="t2", cost_usd=0.50))
    assert ledger.total_spend() == 0.75


def test_discarded_record_roundtrips_reason(tmp_path: Path) -> None:
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record(discarded="judge reply missing criterion 'grounding'"))
    hit = ledger.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
    assert hit is not None and hit.discarded == "judge reply missing criterion 'grounding'"
