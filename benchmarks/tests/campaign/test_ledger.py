"""Resumable queue ledger: completed skip on resume, last-write-wins, spend sum."""

from __future__ import annotations

from pydocs_eval.campaign.ledger import (
    CampaignLedger,
    LedgerRecord,
    WorkItem,
    WorkState,
    build_work,
)


def _done(cell: str, iid: str, cost: float) -> LedgerRecord:
    return LedgerRecord(cell=cell, instance_id=iid, state=WorkState.DONE, cost_usd=cost)


def test_build_work_crosses_cells_and_instances() -> None:
    work = build_work(["a", "b"], ["i1", "i2"])
    assert [w.key for w in work] == [("a", "i1"), ("a", "i2"), ("b", "i1"), ("b", "i2")]


def test_pending_skips_completed(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a"], ["i1", "i2"])
    ledger.record(_done("a", "i1", 1.0))
    pending = ledger.pending(work)
    assert [w.instance_id for w in pending] == ["i2"]


def test_resume_reloads_completed_from_disk(tmp_path) -> None:
    path = tmp_path / "q.jsonl"
    work = build_work(["a"], ["i1", "i2"])
    CampaignLedger(path).record(_done("a", "i1", 2.0))
    # Simulate a kill/resume: a fresh ledger reading the same file.
    resumed = CampaignLedger(path)
    assert resumed.is_completed(WorkItem("a", "i1"))
    assert not resumed.is_completed(WorkItem("a", "i2"))
    assert [w.instance_id for w in resumed.pending(work)] == ["i2"]


def test_excluded_is_terminal(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    ledger.record(LedgerRecord("a", "i1", WorkState.EXCLUDED))
    assert ledger.is_completed(WorkItem("a", "i1"))


def test_running_is_not_terminal_resumes(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    ledger.record(LedgerRecord("a", "i1", WorkState.RUNNING))
    assert not ledger.is_completed(WorkItem("a", "i1"))


def test_last_write_wins(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    ledger.record(LedgerRecord("a", "i1", WorkState.RUNNING))
    ledger.record(_done("a", "i1", 1.5))
    assert ledger.latest(WorkItem("a", "i1")).state is WorkState.DONE


def test_total_spend_sums_all_attempts(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    # An infra retry (cost 0.5) followed by an excluded final (cost 0.7): R8 says
    # both count against the ceiling, so spend is 1.2, not just the latest 0.7.
    ledger.record(LedgerRecord("a", "i1", WorkState.INFRA_RETRY, attempt=1, cost_usd=0.5))
    ledger.record(LedgerRecord("a", "i1", WorkState.EXCLUDED, attempt=1, cost_usd=0.7))
    assert ledger.total_spend() == 1.2


def test_corrupt_trailing_line_skipped(tmp_path) -> None:
    path = tmp_path / "q.jsonl"
    ledger = CampaignLedger(path)
    ledger.record(_done("a", "i1", 1.0))
    with path.open("a") as fh:
        fh.write("{not json\n")
    resumed = CampaignLedger(path)  # must not raise
    assert resumed.is_completed(WorkItem("a", "i1"))


def test_attempt_count_reads_latest(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    assert ledger.attempt_count(WorkItem("a", "i1")) == 0
    ledger.record(LedgerRecord("a", "i1", WorkState.INFRA_RETRY, attempt=1))
    assert ledger.attempt_count(WorkItem("a", "i1")) == 1
