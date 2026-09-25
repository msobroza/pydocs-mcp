"""The ``branch_reindex_cost`` micro-benchmark (#320, spec §6.12 / R21).

It drives the real ``pydocs-mcp index`` then ``index --branch feature/x`` on a
synthetic git repository with a counting embedder, and reports what the second
branch cost against its diff: the counts of the ``branch_reindex`` line (files
total / reused / extracted, chunks embedded / shared, vectors removed) plus the
wall time and the embedder's own count for each run, and a plain ``index``
re-run as the control that isolates the branch pass's time.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

# A pydocs-mcp without multi-branch indexing (0.8.1 and earlier) has no branch
# pass to measure: skip, as the plan's test did, instead of failing collection.
pytest.importorskip("pydocs_mcp.application.branch_pass")
from pydocs_eval.micro.branch_reindex_cost import changed_file_count, main, run

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_SECONDS_FIELDS = {
    "seconds_first_branch",
    "seconds_working_tree_rerun",
    "seconds_second_branch",
    "seconds_branch_pass",
}


@requires_git
def test_the_second_branch_costs_its_diff_and_the_report_names_every_count(
    tmp_path: Path,
) -> None:
    report = run(files=12, changed=2, work_dir=tmp_path)
    counts = report.branch_reindex
    # 12 modules plus the package's empty __init__.py, which has no chunk to reuse.
    assert (counts.files_total, counts.files_reused, counts.files_extracted) == (13, 10, 2)
    # One function edited per changed module: one new chunk each, every other
    # chunk of main shared, nothing freed.
    assert counts.chunks_embedded == report.embeddings_second_branch == 2
    assert counts.chunks_shared == report.embeddings_first_branch - 2 > 0
    assert counts.vectors_removed == 0
    assert (report.files, report.changed, report.changed_percent) == (12, 2, 16.67)


@requires_git
def test_the_branch_pass_time_is_the_branch_run_less_the_working_tree_control(
    tmp_path: Path,
) -> None:
    # The --branch run first repeats the working-tree pass, which re-parses all
    # of main; the plain re-run times that pass alone, so the difference is the
    # branch pass's own share of the wall time.
    report = run(files=4, changed=1, work_dir=tmp_path)
    assert report.seconds_first_branch > 0 and report.seconds_second_branch > 0
    assert report.seconds_working_tree_rerun > 0
    expected = round(report.seconds_second_branch - report.seconds_working_tree_rerun, 3)
    assert report.seconds_branch_pass == expected


@requires_git
def test_the_cli_prints_one_json_report_per_diff_size(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--files", "4", "--changed", "0", "1"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert [report["changed"] for report in reports] == [0, 1]
    unchanged, one_file = (report["branch_reindex"] for report in reports)
    assert (unchanged["files_extracted"], unchanged["chunks_embedded"]) == (0, 0)
    assert (one_file["files_extracted"], one_file["chunks_embedded"]) == (1, 1)
    assert set(one_file) >= {"chunks_shared", "chunks_embedded", "vectors_removed"}
    assert set(reports[0]) >= _SECONDS_FIELDS


def test_a_percent_diff_size_rounds_to_at_least_one_changed_file() -> None:
    assert [changed_file_count(200, percent) for percent in (1, 5, 20)] == [2, 10, 40]
    assert changed_file_count(12, 1) == 1
    assert changed_file_count(12, 0) == 0


def test_sizes_the_repository_cannot_hold_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="changed=4"):
        run(files=3, changed=4, work_dir=tmp_path)
    with pytest.raises(ValueError, match="files=0"):
        run(files=0, changed=0, work_dir=tmp_path)
    with pytest.raises(ValueError, match="150"):
        changed_file_count(10, 150)
    with pytest.raises(SystemExit, match="2"):  # argparse's usage error, before any run
        main(["--files", "3", "--changed", "5"])
