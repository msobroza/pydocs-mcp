"""Trials ledger — (fingerprint, split) resume + spend accounting (plan Task 7)."""

from __future__ import annotations

from pydocs_eval.optimize.trials_ledger import TrialsLedger


def test_record_and_lookup_by_fingerprint_and_split(tmp_path) -> None:
    led = TrialsLedger(tmp_path / "trials.jsonl")
    led.record(fingerprint="f" * 64, split="train", score=0.19, components={"t": 0.2}, cost_usd=2.0)
    hit = led.lookup(fingerprint="f" * 64, split="train")
    assert hit is not None and hit.score == 0.19
    assert led.lookup(fingerprint="f" * 64, split="holdout") is None  # split keys never collide


def test_resume_reads_existing_file(tmp_path) -> None:
    path = tmp_path / "trials.jsonl"
    TrialsLedger(path).record(
        fingerprint="a" * 64, split="train", score=0.1, components={}, cost_usd=1.0
    )
    assert TrialsLedger(path).lookup(fingerprint="a" * 64, split="train") is not None


def test_total_spend_sums_all_entries(tmp_path) -> None:
    led = TrialsLedger(tmp_path / "t.jsonl")
    led.record(fingerprint="a" * 64, split="train", score=0.1, components={}, cost_usd=1.0)
    led.record(fingerprint="b" * 64, split="train", score=0.2, components={}, cost_usd=2.5)
    assert led.total_spend() == 3.5


def test_corrupt_line_skipped_not_fatal(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"fingerprint": "a", "split": "train", "score": 1, "components": {}, "cost_usd": 0}\nnot json\n'
    )
    assert TrialsLedger(path).lookup(fingerprint="a", split="train") is not None


def test_objective_hash_is_part_of_the_resume_key(tmp_path) -> None:
    # AC-12: the same artifact under a different rubric objective never resumes.
    led = TrialsLedger(tmp_path / "t.jsonl")
    led.record(
        fingerprint="a" * 64,
        split="train",
        score=0.4,
        components={},
        cost_usd=1.0,
        objective_hash="o" * 64,
    )
    assert led.lookup(fingerprint="a" * 64, split="train", objective_hash="o" * 64) is not None
    assert led.lookup(fingerprint="a" * 64, split="train", objective_hash="x" * 64) is None
    assert led.lookup(fingerprint="a" * 64, split="train") is None  # None request ≠ hashed line


def test_legacy_lines_without_objective_hash_still_resume(tmp_path) -> None:
    # AC-12: an existing ledger file from before the objective_hash field
    # replays green for fitnesses that return None (legacy back-compat).
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"fingerprint": "a", "split": "train", "score": 1, "components": {}, "cost_usd": 0}\n'
    )
    led = TrialsLedger(path)
    assert led.lookup(fingerprint="a", split="train") is not None
    assert led.lookup(fingerprint="a", split="train", objective_hash="o" * 64) is None


def test_objective_hash_roundtrips_through_the_file(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    TrialsLedger(path).record(
        fingerprint="a" * 64,
        split="train",
        score=0.4,
        components={},
        cost_usd=1.0,
        objective_hash="o" * 64,
    )
    reloaded = TrialsLedger(path)
    hit = reloaded.lookup(fingerprint="a" * 64, split="train", objective_hash="o" * 64)
    assert hit is not None and hit.objective_hash == "o" * 64
