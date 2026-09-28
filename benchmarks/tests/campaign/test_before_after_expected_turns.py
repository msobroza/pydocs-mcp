"""The expected-turns cost model: price a rollout by the turns it is expected to spend.

Unset, the plan still assumes every rollout spends its whole budget, and its text
is byte-identical to before the knob existed. Set, the estimate scales to the
smaller of the expected turns and the cap, and the assumption line says so.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import CostModel, MeasurementPlanError, render_plan

from ._outcome_fixtures import report_plan

# render_plan(report_plan(("t1", "t2"))) as it read before the knob existed.
_ESTIMATE_BEFORE_THE_KNOB = (
    "estimate:   4 rollout(s), up to 48 model turn(s), ~88 tool call(s)\n"
    "            ~196560 input + ~19200 output tokens\n"
    "            ~$0.00 — no price given; pass --usd-per-1m-input / --usd-per-1m-output\n"
    "assumptions (none of these is measured):\n"
    "  - every rollout spends its full 12-turn budget\n"
    "  - 2.0 tool call(s) per tool-calling turn\n"
    "  - 4000 context tokens per turn, plus that arm's description surface\n"
    "  - 400 output tokens per turn"
)


def _plan_expecting(turns: float | None) -> str:
    plan = report_plan(("t1", "t2"))
    return render_plan(dataclasses.replace(plan, cost=CostModel(expected_turns_per_rollout=turns)))


def test_unset_expected_turns_leave_the_plan_byte_identical() -> None:
    assert _ESTIMATE_BEFORE_THE_KNOB in _plan_expecting(None)


def test_expected_turns_scale_the_estimate_and_name_the_assumption() -> None:
    text = _plan_expecting(5)

    # 4 rollouts x 5 turns; each spends 4 tool-calling turns at 2.0 calls.
    assert "estimate:   4 rollout(s), ~20 model turn(s), ~32 tool call(s)\n" in text
    assert "            ~81900 input + ~8000 output tokens\n" in text
    assert (
        "  - each rollout spends ~5 of its 12-turn budget (--expected-turns-per-rollout)\n" in text
    )
    assert "every rollout spends its full" not in text


def test_expected_turns_above_the_cap_price_the_cap() -> None:
    text = _plan_expecting(30)

    assert "~48 model turn(s)" in text
    assert "each rollout spends ~12 of its 12-turn budget" in text


@pytest.mark.parametrize("turns", [0, -2.0])
def test_a_non_positive_expected_turn_count_is_refused_by_value(turns: float) -> None:
    with pytest.raises(MeasurementPlanError, match=f"expected_turns_per_rollout = {turns!r}"):
        CostModel(expected_turns_per_rollout=turns)


def test_the_flag_prices_the_printed_plan(
    tmp_path: Path, stub_command: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stub serves a 4-turn budget over two tasks: 4 rollouts x 2.5 turns."""
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo, "--expected-turns-per-rollout", "2.5")) == 0

    printed = capsys.readouterr().out
    assert "~10 model turn(s)" in printed
    assert "each rollout spends ~2.5 of its 4-turn budget" in printed


def test_the_flag_refuses_a_non_positive_count_by_value(
    tmp_path: Path, stub_command: object, capsys: pytest.CaptureFixture[str]
) -> None:
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo, "--expected-turns-per-rollout", "0")) == 2
    assert "expected_turns_per_rollout = 0.0" in capsys.readouterr().err
