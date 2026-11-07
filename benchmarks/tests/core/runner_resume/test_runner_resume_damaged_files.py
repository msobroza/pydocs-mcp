"""``--run-id``: a run file a killed process left damaged still resumes (issue #420).

A truncated last line reruns its task; ``ci_compare`` reads a resumed file's
last aggregate and still refuses one whose last invocation died mid-line.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydocs_eval.reporting import ci_compare

from ._resume_fakes import (
    COUNTING_SYSTEM,
    FAKE_DATASET,
    FAKE_DATASET_SIZE,
    TASK_SCORE_METRIC,
    CountingFakeSystem,
    reset_resume_fakes,
)
from ._resume_sweep import (
    LEG,
    all_task_ids,
    events_of,
    interrupted,
    leg_file,
    parses,
    queries,
    sweep,
    task_ids_of,
    truncate_last_task_record,
)

# ── Truncated trailing line ──────────────────────────────────────────────


async def test_truncated_last_line_reruns_its_task_with_a_warning(
    tmp_path: Path, overlay: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await interrupted(tmp_path, overlay, fail_at=4, run_id="cut")
    path = leg_file(tmp_path, "cut")
    truncate_last_task_record(path)  # task 3's record is now a fragment

    with caplog.at_level(logging.WARNING):
        outcome = await sweep(tmp_path, overlay, run_id="cut")

    assert CountingFakeSystem.searched == queries(3)
    assert any("corrupt_line_skipped" in r.getMessage() for r in caplog.records)
    lines = path.read_text().splitlines()
    fragment = next(i for i, line in enumerate(lines) if not parses(line))
    # The records appended after the fragment start on a line of their own.
    assert json.loads(lines[fragment + 1])["_event"] == "run_resume"
    assert all(parses(line) for i, line in enumerate(lines) if i != fragment)
    records = [json.loads(line) for line in lines if parses(line)]
    assert task_ids_of(records) == all_task_ids()
    assert events_of(records, "run_resume")[0]["recorded_tasks"] == 3
    assert [o.task_id for o in outcome.legs[LEG].observations] == all_task_ids()

    # A later resume skips the now mid-file fragment again and runs nothing.
    reset_resume_fakes()
    again = await sweep(tmp_path, overlay, run_id="cut")
    assert CountingFakeSystem.searched == []
    assert again.results == outcome.results


# ── Truncated header (finding F7) ────────────────────────────────────────


def _cut_header_short(path: Path) -> None:
    """Leave only the first half of the ``run_start`` header, as a kill mid-header does."""
    header = path.read_text().splitlines()[0]
    path.write_text(header[: len(header) // 2])  # no newline, nothing after it


async def test_a_header_cut_short_with_no_task_records_starts_the_leg_afresh(
    tmp_path: Path, overlay: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await interrupted(tmp_path, overlay, fail_at=0, run_id="cut-header")
    path = leg_file(tmp_path, "cut-header")
    _cut_header_short(path)

    with caplog.at_level(logging.WARNING):
        outcome = await sweep(tmp_path, overlay, run_id="cut-header")

    assert CountingFakeSystem.searched == queries(0)
    assert [o.task_id for o in outcome.legs[LEG].observations] == all_task_ids()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("named_run_without_header" in m and str(path) in m for m in warnings), warnings
    # The leg was rewritten from scratch: one whole header, no resume, no fragment.
    lines = path.read_text().splitlines()
    assert all(parses(line) for line in lines)
    records = [json.loads(line) for line in lines]
    assert records[0]["_event"] == "run_start" and "identity" in records[0]
    assert events_of(records, "run_start") == [records[0]]
    assert events_of(records, "run_resume") == []
    assert task_ids_of(records) == all_task_ids()


async def test_task_records_without_a_header_still_refuse_the_resume(
    tmp_path: Path, overlay: Path
) -> None:
    await interrupted(tmp_path, overlay, fail_at=3, run_id="headless")
    path = leg_file(tmp_path, "headless")
    lines = path.read_text().splitlines()
    _, *rest = lines
    path.write_text(lines[0][: len(lines[0]) // 2] + "\n" + "\n".join(rest) + "\n")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="no run_start header") as excinfo:
        await sweep(tmp_path, overlay, run_id="headless")

    assert str(path) in str(excinfo.value)
    assert CountingFakeSystem.searched == []
    assert path.read_bytes() == before


# ── ci_compare over a resumed file ───────────────────────────────────────


async def test_ci_compare_reads_the_last_aggregate_of_a_resumed_file(
    tmp_path: Path, overlay: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await interrupted(tmp_path, overlay, fail_at=4, run_id="ci")
    path = leg_file(tmp_path, "ci")
    truncate_last_task_record(path)  # the earlier invocation died mid-line
    outcome = await sweep(tmp_path, overlay, run_id="ci")
    mean = outcome.results[LEG][TASK_SCORE_METRIC][0]
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "system": COUNTING_SYSTEM,
                "config": "baseline",
                "dataset": FAKE_DATASET,
                "tasks_ran": FAKE_DATASET_SIZE,
                "metrics": {TASK_SCORE_METRIC: {"mean": mean}},
            }
        )
    )
    argv = ["ci_compare", "--baseline", str(baseline), "--current", str(path)]
    monkeypatch.setattr("sys.argv", [*argv, "--metric", TASK_SCORE_METRIC])

    assert ci_compare.main() == 0


async def test_ci_compare_still_refuses_a_file_whose_last_invocation_died_mid_line(
    tmp_path: Path, overlay: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await interrupted(tmp_path, overlay, fail_at=4, run_id="died")
    path = leg_file(tmp_path, "died")
    truncate_last_task_record(path)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"system": "s", "config": "c", "dataset": "d", "tasks_ran": 1,
                    "metrics": {TASK_SCORE_METRIC: {"mean": 0.5}}})
    )  # fmt: skip
    argv = ["ci_compare", "--baseline", str(baseline), "--current", str(path)]
    monkeypatch.setattr("sys.argv", [*argv, "--metric", TASK_SCORE_METRIC])

    assert ci_compare.main() == 2
