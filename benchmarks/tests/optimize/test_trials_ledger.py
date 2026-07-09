"""Trials ledger — (fingerprint, split) resume + spend accounting (plan Task 7)."""

from __future__ import annotations

from benchmarks.optimize.trials_ledger import TrialsLedger


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
