"""``before-after-compare --guidance-task-clauses``: the flip clauses printed under each verdict.

The verb end to end over fixture arm directories, built with the compare
tests' own writers (``test_before_after_compare``): ten tasks per arm, the A/A
replicate the baseline itself. The clause logic is pinned by the truth table in
``test_before_after_flip_clauses``; here the mode is read as an operator reads it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_compare_command import add_before_after_compare_command
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

from ._fakes import FakeSplitTasks
from .test_before_after_compare import _Row, _rows, _run, _write_arm

_MODE = "--guidance-task-clauses"
_EDGE = ("--noise-edge", "0.6")


def _lines_after(report: str, prefix: str, count: int) -> list[str]:
    """The ``count`` lines right after the one starting with ``prefix``."""
    lines = report.splitlines()
    [index] = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    return lines[index + 1 : index + 1 + count]


def test_the_mode_leaves_the_verdict_its_reasons_and_the_exit_status_alone(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))
    slower = _write_arm(tmp_path, "slower", _rows(turns=(7,) * 10))

    plain_code, plain = _run(capsys, baseline, faster, slower)
    mode_code, with_mode = _run(capsys, baseline, faster, slower, extra=(_MODE, *_EDGE))

    assert (plain_code, mode_code) == (1, 1)
    mode_lines = set(with_mode.splitlines())
    changed = [line for line in plain.splitlines() if line not in mode_lines]
    assert [line for line in changed if not line.startswith("- rule: ")] == []


def test_each_verdict_line_is_followed_by_one_finding_per_clause_in_reading_order(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    code, report = _run(capsys, baseline, faster, extra=(_MODE, *_EDGE))

    assert code == 0
    findings = _lines_after(report, f"- `{faster}`: PASS (Q10)", 3)
    assert [line.split(":", 1)[0] for line in findings] == [
        "  - clause (b) unanswered outcomes",
        "  - clause (c) minimum gain",
        "  - clause (a) tokens",
    ]
    assert findings[0].startswith("  - clause (b) unanswered outcomes: held")
    assert "fell by 1.000, at or above the noise edge 0.600" in findings[1]
    # The fixture traces recorded no usage, so the tokens clause has nothing to read.
    assert findings[2].startswith("  - clause (a) tokens: not read (tokens in (per task) n/a")


def test_the_rule_line_names_the_added_clauses(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    _code, report = _run(capsys, baseline, faster, extra=(_MODE,))

    [rule] = [line for line in report.splitlines() if line.startswith("- rule: ")]
    assert rule.startswith("- rule: standard (Q10): ")
    assert (
        "then the guidance-task flip clauses, for this experiment only and never changing "
        "the verdict: (b) unanswered outcomes, (c) minimum gain (no noise edge given: "
        "not evaluated), (a) tokens"
    ) in rule


def test_a_failed_variant_gets_findings_that_say_the_clauses_were_not_read(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    slower = _write_arm(tmp_path, "slower", _rows(turns=(7,) * 10))

    code, report = _run(capsys, baseline, slower, extra=(_MODE, *_EDGE))

    assert code == 1
    findings = _lines_after(report, f"- `{slower}`: FAIL (Q10)", 3)
    assert all("not read (the standard rule gave FAIL, not PASS)" in line for line in findings)


def test_a_starved_reply_more_than_the_baseline_prints_no_flip(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows(turns=(8,) * 10))
    starved = [*_rows(turns=(5,) * 10)[:9], _Row(turns=8, outcome=TaskOutcome.STARVED_REPLY)]
    variant = _write_arm(tmp_path, "starved", starved)

    code, report = _run(capsys, baseline, variant, extra=(_MODE,))

    assert code == 0
    [unanswered] = _lines_after(report, f"- `{variant}`: PASS (Q10)", 1)
    assert unanswered.startswith(
        "  - clause (b) unanswered outcomes: no flip (starved_reply 0 → 1, timeout 0 → 0, "
        "unanswered_empty 0 → 0"
    )


def test_the_mode_names_the_pre_registered_rows_for_each_variant(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    _code, report = _run(capsys, baseline, faster, extra=(_MODE,))

    section = report.split("### Pre-registered rows (reported, not gating)", 1)[1]
    assert f"- `{faster}` against `{baseline}`, baseline → variant:" in section
    for row in (
        "search calls 1 → 1 (expected: not up)",
        "turns after needle ",
        "calls after first gold (per task) ",
        "calls after first gold read (per task) ",
        "turns before the needle (the headline less turns after needle) ",
        "tokens in (per task) n/a",
        "uncached tokens in (total) n/a",
        "uncached tokens in (per turn) n/a",
    ):
        assert f"  - {row}" in section, row
    [tally] = [line for line in section.splitlines() if line.startswith("  - outcomes: ")]
    assert tally.count(" → ") == len(TaskOutcome)
    assert "answered 10 → 10" in tally


def test_without_the_mode_there_are_no_clauses_and_no_pre_registered_rows(
    tmp_path: Path, compare_split: FakeSplitTasks, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    _code, report = _run(capsys, baseline, faster)

    assert "clause (" not in report
    assert "Pre-registered" not in report
    assert "flip clauses" not in report


@pytest.mark.parametrize(
    ("extra", "refusal"),
    [
        ((_MODE, "--completeness-arm"), "--guidance-task-clauses and --completeness-arm"),
        (_EDGE, "--noise-edge needs --guidance-task-clauses"),
        ((_MODE, "--noise-edge", "0"), "--noise-edge must be positive, got 0.0"),
    ],
)
def test_an_inconsistent_mode_is_refused_as_an_input_error(
    tmp_path: Path,
    compare_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    refusal: str,
) -> None:
    baseline = _write_arm(tmp_path, "baseline", _rows())
    faster = _write_arm(tmp_path, "faster", _rows(turns=(5,) * 10))

    code, report = _run(capsys, baseline, faster, extra=extra)

    assert code == 2
    assert refusal in report
    assert "## Before/after comparison" not in report


def test_the_help_says_the_mode_is_this_experiments_and_the_edge_is_left_out_of_a_rerun() -> None:
    subparsers = argparse.ArgumentParser().add_subparsers()
    add_before_after_compare_command(subparsers)
    help_text = " ".join(subparsers.choices["before-after-compare"].format_help().split())

    assert "guidance-task experiment (#391) only" in help_text
    assert "omit it when reading the re-run on repoqa-qa/dev" in help_text
