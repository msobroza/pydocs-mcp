"""Sample-level rubric ledger — per-sample resume sidecar (spec AC-11, AC-12)."""

from __future__ import annotations

import json
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
    tracked: dict[str, float] | None = None,
    checks: dict[str, float] | None = None,
    arm_hash: str = "",
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
        tracked=dict(tracked or {}),
        checks=dict(checks or {}),
        arm_hash=arm_hash,
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


def test_tracked_metrics_roundtrip_as_sibling_fields(tmp_path: Path) -> None:
    # An arm's observational metrics ride the ledger line beside the verdict —
    # recorded per sample, never part of the resume key.
    path = tmp_path / "samples.jsonl"
    SampleRubricLedger(path).record(_record(tracked={"gold_recall": 0.5}))
    hit = SampleRubricLedger(path).lookup(
        fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64
    )
    assert hit is not None and hit.tracked == {"gold_recall": 0.5}


def test_a_line_written_before_tracked_existed_still_parses(tmp_path: Path) -> None:
    # The ``.get``-tolerant sibling-field pattern: adding an observational
    # field must never orphan a ledger someone already paid for.
    path = tmp_path / "samples.jsonl"
    SampleRubricLedger(path).record(_record())
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line.pop("tracked")
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    hit = SampleRubricLedger(path).lookup(
        fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64
    )
    assert hit is not None and hit.tracked == {}


def test_two_arms_never_resume_each_others_rows(tmp_path: Path) -> None:
    # Run-contract design §6: the shipped arm pair shares a candidate, a split,
    # a task AND an objective and differs only in tool_names — without the arm
    # component the second arm would resume the first arm's verdict for free.
    path = tmp_path / "samples.jsonl"
    ledger = SampleRubricLedger(path)
    ledger.record(_record(arm_hash="a" * 64, verdict=0.9))
    ledger.record(_record(arm_hash="b" * 64, verdict=0.1))

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    keys = dict(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
    assert ledger.lookup(**keys, arm_hash="a" * 64).verdict == 0.9
    assert ledger.lookup(**keys, arm_hash="b" * 64).verdict == 0.1
    # And a row written under NO arm belongs to neither of them.
    assert ledger.lookup(**keys) is None


def test_a_single_implicit_arm_line_keeps_the_legacy_byte_shape(tmp_path: Path) -> None:
    # The trials-ledger rule, applied here: an arm-less run writes the EXACT
    # pre-``arms:`` line, so a sidecar written by this version still parses
    # under the previous reader — which rebuilds with SampleRubricRecord(**line)
    # and would reject every line as corrupt on an unknown kwarg, re-paying the
    # whole run.
    path = tmp_path / "samples.jsonl"
    SampleRubricLedger(path).record(_record())
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines and all("arm_hash" not in line for line in lines)


def test_a_line_written_before_arm_hash_existed_matches_only_the_implicit_arm(
    tmp_path: Path,
) -> None:
    # The ``.get``-tolerant sibling pattern: a legacy row parses, keeps
    # resuming the single implicit arm, and can never match a real arm hash.
    path = tmp_path / "samples.jsonl"
    SampleRubricLedger(path).record(_record())
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line.pop("arm_hash", None)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    reloaded = SampleRubricLedger(path)
    keys = dict(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
    assert reloaded.lookup(**keys) is not None
    assert reloaded.lookup(**keys, arm_hash="a" * 64) is None


def test_discarded_record_roundtrips_reason(tmp_path: Path) -> None:
    ledger = SampleRubricLedger(tmp_path / "samples.jsonl")
    ledger.record(_record(discarded="judge reply missing criterion 'grounding'"))
    hit = ledger.lookup(fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64)
    assert hit is not None and hit.discarded == "judge reply missing criterion 'grounding'"


def test_scored_check_values_roundtrip_as_sibling_fields(tmp_path: Path) -> None:
    """The deterministic composite must stay decomposable after the fact."""
    ledger = SampleRubricLedger(tmp_path / "s.jsonl")
    ledger.record(_record(checks={"gold_recall": 0.5, "gold_location_evidence": 1.0}))

    reloaded = SampleRubricLedger(tmp_path / "s.jsonl")
    hit = reloaded.lookup(
        fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64
    )
    assert hit is not None
    assert hit.checks == {"gold_recall": 0.5, "gold_location_evidence": 1.0}


def test_a_gates_only_line_keeps_the_pre_checks_byte_shape(tmp_path: Path) -> None:
    """An objective configuring no checks must not move one ledger byte.

    The previous reader reconstructs with ``SampleRubricRecord(**line)`` and
    rejects every line as corrupt on an unknown kwarg — re-paying the run.
    """
    ledger = SampleRubricLedger(tmp_path / "s.jsonl")
    ledger.record(_record())

    lines = (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines and all("checks" not in line for line in lines)


def test_a_line_written_before_checks_existed_still_parses(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    payload = {
        "fingerprint": "f" * 64,
        "split": "train",
        "task_id": "t1",
        "qa_type": "how",
        "objective_hash": "o" * 64,
        "gates": {"non_empty": True},
        "gate_pass_fraction": 1.0,
        "judge_skipped": False,
        "criteria": {},
        "rubric_score": 0.0,
        "verdict": 0.3,
        "turns": 1,
        "wall_seconds": 1.0,
        "cost_usd": 0.0,
        "answer_sha256": "a" * 64,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    hit = SampleRubricLedger(path).lookup(
        fingerprint="f" * 64, split="train", task_id="t1", objective_hash="o" * 64
    )
    assert hit is not None
    assert hit.checks == {}
