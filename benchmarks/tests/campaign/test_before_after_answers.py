"""The answer rows: each stored answer scored against its needle by code, then reported.

``needle cited`` is the correctness guard the acceptance rule reads, so it is
computed from what the run persisted — never by a judge — and reads undefined,
never zero, for a row written before answers were stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_answers import needle_sites_by_task, needle_sites_of
from pydocs_eval.campaign.before_after_arm import write_arm_summary
from pydocs_eval.campaign.before_after_measure import TaskMeasurement, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.repo_qa import GOLD_SYMBOL_KEY
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.trajectory.ask_outcome import TaskOutcome
from tests.trajectory._ask_traces import write_ask_trajectory

from ._outcome_fixtures import (
    BASELINE,
    CANDIDATE,
    arm_of,
    arm_record,
    arm_summary,
    needle_trace,
    report_plan,
    row_cells,
)

_FIND = NeedleSite("pkg/needle.py", "find")
_LOAD = NeedleSite("pkg/other.py", "load")
_USAGE = NeedleSite("docs/usage.md", "find_all")


def _task(
    file_set: tuple[str, ...],
    *,
    extra: dict[str, object] | None = None,
    metadata: dict[str, str] | None = None,
) -> EvalTask:
    return EvalTask(
        task_id="t1",
        query="Where?",
        gold=GoldAnswer(file_set=file_set, extra=extra or {}),
        corpus_source=lambda: Path("unused"),
        metadata=metadata or {},
    )


def test_a_chat_record_pairs_each_gold_span_with_its_symbol() -> None:
    task = _task(
        ("src/a.py", "README.md"),
        extra={"symbol_0": "alpha", "symbol_1": "beta", "symbol_2": "Usage"},
        metadata={"site_0": "src/a.py:3-9", "site_1": "src/a.py:20-24", "site_2": "README.md:1-5"},
    )

    assert needle_sites_of(task) == (
        NeedleSite("src/a.py", "alpha"),
        NeedleSite("src/a.py", "beta"),
        NeedleSite("README.md", "Usage"),
    )


def test_a_repoqa_needle_is_its_file_and_function() -> None:
    task = _task(("sklearn/base.py",), extra={GOLD_SYMBOL_KEY: "get_params"})

    assert needle_sites_of(task) == (NeedleSite("sklearn/base.py", "get_params"),)


def test_a_file_set_gold_is_one_site_per_file() -> None:
    assert needle_sites_of(_task(("a.py", "b/c.py"))) == (NeedleSite("a.py"), NeedleSite("b/c.py"))


def test_the_sites_are_keyed_by_task_id() -> None:
    task = _task(("a.py",))

    assert needle_sites_by_task([task]) == {"t1": (NeedleSite("a.py"),)}


def _measured(
    tmp_path: Path,
    answer: str,
    sites: tuple[NeedleSite, ...],
    *,
    outcome: TaskOutcome = TaskOutcome.ANSWERED,
    jev: JevConfig | None = None,
    task_id: str = "t1",
    trace_dir: Path | None = None,
    gold_files: tuple[str, ...] | None = None,
) -> TaskMeasurement:
    record = arm_record(
        trace_dir or needle_trace(tmp_path),
        task_id=task_id,
        answer=answer,
        answer_chars=len(answer),
        outcome=outcome,
        gold_files=list(gold_files or dict.fromkeys(site.path for site in sites)),
    )
    [task] = measure_arm(
        arm_summary(record),
        BASELINE,
        workspace=Path("/ws"),
        needle_sites={task_id: sites},
        jev=jev or JevConfig(),
    ).per_task
    return task


@pytest.mark.parametrize(
    ("answer", "cited"),
    [("It is `pkg.needle.find`.", 1), ("It is `pkg.other.load`.", 0)],
)
def test_the_stored_answer_is_scored_against_its_needle(
    tmp_path: Path, answer: str, cited: int
) -> None:
    assert _measured(tmp_path, answer, (_FIND,)).answer.needle_cited == cited


def test_a_run_that_ended_unanswered_cites_nothing(tmp_path: Path) -> None:
    measured = _measured(tmp_path, "", (_FIND,), outcome=TaskOutcome.BUDGET_EXHAUSTED)

    assert measured.answer.needle_cited == 0


def test_a_row_stored_before_answers_is_undefined_never_zero(tmp_path: Path) -> None:
    measured = _measured(tmp_path, "", (_FIND,), outcome=TaskOutcome.UNRECORDED)

    assert measured.answer.needle_cited is None
    assert measured.answer.answer_over_cap is None


def test_a_task_whose_needle_is_unknown_is_undefined(tmp_path: Path) -> None:
    record = arm_record(needle_trace(tmp_path), answer="It is `pkg.needle.find`.")

    [measured] = measure_arm(arm_summary(record), BASELINE, workspace=Path("/ws")).per_task

    assert measured.answer.needle_cited is None


def test_coverage_and_precision_read_only_on_a_multi_location_needle(tmp_path: Path) -> None:
    answer = "It is `pkg.needle.find`, in `pkg/needle.py` and `pkg/extra.py`."

    single = _measured(tmp_path, answer, (_FIND,))
    multi = _measured(tmp_path, answer, (_FIND, _LOAD))

    assert (single.answer.gold_site_coverage, single.answer.cited_path_precision) == (None, None)
    assert (multi.answer.gold_site_coverage, multi.answer.cited_path_precision) == (0.5, 0.5)


def test_an_answer_longer_than_the_judge_cap_is_flagged(tmp_path: Path) -> None:
    capped = JevConfig(max_answer_chars=10)

    assert (
        _measured(tmp_path, "`pkg.needle.find`", (_FIND,), jev=capped).answer.answer_over_cap == 1
    )
    assert _measured(tmp_path, "`find`", (_FIND,), jev=capped).answer.answer_over_cap == 0


def test_calls_to_full_coverage_count_to_the_last_gold_file(tmp_path: Path) -> None:
    trace = write_ask_trajectory(
        tmp_path / "traces",
        calls=[("search_codebase", {"query": "q"}, 1)] * 3,
        items_per_call=[
            [{"path": "pkg/needle.py"}],
            [{"path": "x.py"}],
            [{"path": "pkg/other.py"}],
        ],
    )

    multi = _measured(tmp_path, "`find`", (_FIND, _LOAD), trace_dir=trace)
    single = _measured(tmp_path, "`find`", (_FIND,), trace_dir=trace)

    assert multi.tool_calls_to_full_gold_coverage == 3
    assert single.tool_calls_to_full_gold_coverage is None, "one file: first gold says it all"


_ONE, _TWO, _THREE = (_FIND,), (_FIND, _LOAD), (_FIND, _LOAD, _USAGE)
_SITES = {"t1": _ONE, "t2": _TWO, "t3": _THREE}


def _arm(tmp_path: Path, commit: object, answers: dict[str, str]) -> object:
    measured = [
        _measured(tmp_path, answer, _SITES[task_id], task_id=task_id)
        for task_id, answer in answers.items()
    ]
    return arm_of(commit, *measured)  # type: ignore[arg-type]


def test_the_answer_rows_on_one_two_and_three_site_needles(tmp_path: Path) -> None:
    baseline = _arm(
        tmp_path,
        BASELINE,
        {
            "t1": "It is `pkg.needle.find`.",  # 1/1
            "t2": "It is `pkg.needle.find`.",  # 1/2
            "t3": "`pkg.needle.find`, `pkg.other.load` and docs/usage.md.",  # 3/3
        },
    )
    candidate = _arm(
        tmp_path,
        CANDIDATE,
        {
            "t1": "`find` in `pkg/needle.py`.",  # 1/1
            "t2": "`pkg.needle.find` and `pkg.other.load`.",  # 2/2
            "t3": "It is `pkg.needle.find`.",  # 1/3
        },
    )

    report = render_report(report_plan(("t1", "t2", "t3")), [baseline, candidate])

    cited = row_cells(report, "needle cited")
    coverage = row_cells(report, "gold-site coverage at stop")
    assert cited[1].startswith("0.667 ") and cited[2].startswith("0.667 ")
    assert coverage[1].startswith("0.750 ") and coverage[2].startswith("0.667 ")
    assert row_cells(report, "answers over the judge cap")[1:3] == ["0", "0"]


def test_an_arm_without_stored_answers_reads_not_available(tmp_path: Path) -> None:
    legacy = _measured(tmp_path, "", _ONE, outcome=TaskOutcome.UNRECORDED)
    arm = arm_of(BASELINE, legacy)

    report = render_report(report_plan(("t1",)), [arm, arm])

    assert row_cells(report, "needle cited")[1] == "n/a"
    assert row_cells(report, "answers over the judge cap")[1] == "n/a"


def test_report_only_scores_the_answers_a_finished_run_stored(
    tmp_path: Path, stub_command: object
) -> None:
    """The needle comes from the split's gold (``a.py`` in this stub), the answer from arm.json."""
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)
    out_dir = tmp_path / "out"
    trace = needle_trace(tmp_path)
    for role, answer in (("baseline", "It is in `a.py`."), ("candidate", "I could not find it.")):
        (out_dir / role).mkdir(parents=True)
        record = arm_record(trace, answer=answer, answer_chars=len(answer))
        write_arm_summary(out_dir / role, arm_summary(record))

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 0

    report = (out_dir / "before_after.md").read_text()
    assert row_cells(report, "needle cited")[1:3] == ["1 [1, 1]", "0 [0, 0]"]
