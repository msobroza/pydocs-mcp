"""Trials ledger — (fingerprint, split, objective, arm) resume + spend accounting."""

from __future__ import annotations

import json

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


def _arm_entry(led: TrialsLedger, arm_hash: str, score: float) -> None:
    led.record(
        fingerprint="a" * 64,
        split="holdout",
        score=score,
        components={},
        cost_usd=1.0,
        objective_hash="o" * 64,
        arm_hash=arm_hash,
    )


def test_two_arms_sharing_one_objective_never_resume_each_other(tmp_path) -> None:
    # Run-contract design §6: every arm of a run scores the same candidate on
    # the same splits under the same objective, so without the arm component
    # the second arm's gate would read the first arm's score for free.
    led = TrialsLedger(tmp_path / "t.jsonl")
    _arm_entry(led, "1" * 64, 0.9)
    _arm_entry(led, "2" * 64, 0.1)
    keys = dict(fingerprint="a" * 64, split="holdout", objective_hash="o" * 64)
    assert led.lookup(**keys, arm_hash="1" * 64).score == 0.9
    assert led.lookup(**keys, arm_hash="2" * 64).score == 0.1
    assert led.lookup(**keys) is None
    # One file, one spend pool — that is what keeps budget.max_usd from being
    # multiplied by the number of arms.
    assert led.total_spend() == 2.0


def test_a_single_implicit_arm_line_keeps_the_legacy_byte_shape(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    TrialsLedger(path).record(
        fingerprint="a" * 64, split="train", score=0.4, components={}, cost_usd=1.0
    )
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "arm_hash" not in written  # replays under the previous reader
    assert TrialsLedger(path).lookup(fingerprint="a" * 64, split="train") is not None
