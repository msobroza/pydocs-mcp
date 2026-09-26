"""The words around the before/after report's table: its caveat bullets and reading notes.

``before_after_report`` renders the numbers; this module holds the prose that
says how to take them — the header bullet an arm gets when its product wrote no
per-turn or no usage sidecar, and the paragraphs under the table: the success
criterion, what the outcome and turn rows count, the statistics, what ``n/a``
means, how spend is measured and which definition of a used call applied. The
functions here only assemble that prose; every figure it quotes arrives already
computed. Kept apart from the table so the rendering and the wording around it
change for their own reasons.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_eval.campaign.before_after import ArmRole, MeasurementPlan
from pydocs_eval.campaign.before_after_measure import ArmMetrics
from pydocs_eval.campaign.before_after_rows import TAIL_LABEL, MetricDirection
from pydocs_eval.trajectory.ask_outcome import unanswered_penalty
from pydocs_eval.trajectory.tool_usage import UsedCallDefinition

#: How the report prints an undefined value: in a table cell, and wherever its
#: prose quotes that cell.
UNDEFINED_CELL = "n/a"


def no_recorded_turns_bullet(role: ArmRole, arm: ArmMetrics) -> str:
    """Why that arm's per-turn rows read ``n/a`` and its needless rate is a floor."""
    return (
        f"- per-turn metrics, {role}: {arm.tasks_without_recorded_turns} of "
        f"{arm.trajectories} measured task(s) recorded NO per-turn sidecar — that "
        f"commit's product predates it. Fan-out-where-batch is therefore unmeasured "
        f"for {role} (its row reads `{UNDEFINED_CELL}`), `parallel calls per turn` and "
        f"`turns after needle` read `{UNDEFINED_CELL}` for it too, and its needless-call "
        "rate counts only the other three components, which makes that rate a LOWER "
        f"BOUND — the true rate can only be higher. {_LOWER_BOUND_READING[role]}"
    )


def no_recorded_usage_bullet(role: ArmRole, arm: ArmMetrics) -> str:
    """Why that arm's spend rows read ``n/a`` — never a free run."""
    return (
        f"- spend, {role}: {arm.tasks_without_recorded_usage} of {arm.trajectories} "
        "measured task(s) recorded NO usage sidecar — that commit's product predates it. "
        f"Its token, cached-token, uncached-token and cost rows read `{UNDEFINED_CELL}` "
        "for those tasks, which pair with nothing: undefined, not a zero spend."
    )


# How an understated arm bends the contrast, per side. The needless-call rate is
# defined for every trajectory, so no pair is dropped: all the delta and p lose is
# the fan-out share of ONE arm's rate, which moves the delta in a known direction.
_LOWER_BOUND_READING: Mapping[ArmRole, str] = {
    ArmRole.BASELINE: (
        "The paired delta and p still rest on every task both arms defined (this rate "
        "is defined for every trajectory, so no pair is dropped), and since only the "
        "baseline is understated, a reported DECREASE in the needless-call rate is "
        "conservative: the real decrease can only be larger."
    ),
    ArmRole.CANDIDATE: (
        "The paired delta and p still rest on every task both arms defined (this rate "
        "is defined for every trajectory, so no pair is dropped), but since the "
        "CANDIDATE is the understated side, a reported DECREASE in the needless-call "
        "rate is an upper bound on the improvement: the real decrease can only be smaller."
    ),
}


def reading_lines(plan: MeasurementPlan, baseline: ArmMetrics) -> list[str]:
    """How to read the table — the success criterion, the statistics, the gaps.

    Also what the outcome and turn rows count, and why an unanswered task counts
    cap + 1.
    """
    used = baseline.used_definition
    penalty = unanswered_penalty(plan.max_agent_turns)
    return [
        _DIRECTION_READING,
        "",
        _OUTCOME_ROWS_READING,
        "",
        _TURN_ROWS_READING.format(penalty=penalty),
        "",
        _STATISTICS_READING,
        "",
        _UNDEFINED_READING,
        "",
        _SPEND_READING,
        "",
        f"Used calls are counted under the `{used}` definition: " + _USED_DEFINITION_NOTES[used],
    ]


# The reading paragraphs, in the order the report prints them. Module-level
# prose, like the notes below: the functions only assemble them.
_DIRECTION_READING = (
    f"`{MetricDirection.LOWER_IS_BETTER}` marks a metric that is better lower, "
    f"`{MetricDirection.HIGHER_IS_BETTER}` one that is better higher, "
    f"`{MetricDirection.NEUTRAL}` one that is neither (so it carries no p). "
    "The change succeeds when the needless-call rate goes DOWN while tool calls "
    "to first gold stay flat or improve."
)
_STATISTICS_READING = (
    "Each arm's cell is its mean with a 95% percentile-bootstrap interval "
    "(1000 resamples, seed 0). `delta` is the PAIRED change, candidate minus "
    "baseline, over the `pairs` tasks both arms measured — so it can differ "
    "from the difference of the two columns, which average each arm's own "
    "defined tasks. `p` is one-sided for the candidate being better in that "
    "row's own direction (Wilcoxon signed-rank; McNemar's exact two-sided p "
    "for the 0/1 rates). A count or total row is a whole-arm figure and a "
    f"`({TAIL_LABEL})` row each arm's {TAIL_LABEL} over the tasks that defined "
    "it; neither carries a test."
)
_UNDEFINED_READING = (
    f"`{UNDEFINED_CELL}` means undefined, not zero: a rate over opportunities the "
    "server created is undefined when there were none, a retrieval number is "
    "undefined when the trajectory never searched, and such trajectories are "
    "dropped from the mean rather than counted as zero."
)
_SPEND_READING = (
    "The spend rows are MEASURED, not assumed. Reasoning tokens are the thinking "
    "slice of tokens out and cached tokens the reused slice of tokens in, so "
    "neither is added to its parent. Usage is counted once per model message id, "
    "so a message an endpoint re-sent on a retry is billed once. `estimated USD` "
    "prices the measured tokens with the run's `--usd-per-1m-*` flags (reasoning "
    "at the output rate, since the endpoint bills it as completion); `reported "
    f"USD` is the endpoint's own quote, `{UNDEFINED_CELL}` when it quoted none."
)


# What the outcome rows count, in the order the taxonomy decides them.
_OUTCOME_ROWS_READING = (
    "Every task ends in exactly ONE outcome, decided in this order: `timeout` (the "
    "eval's per-task timeout killed it), `budget_exhausted` (the turn budget ran "
    "out and no answer came back), `exhausted_finalized` (it ran out and one final "
    "reply still answered), `starved_reply` (an empty reply the endpoint cut at its "
    "token limit while the model could think), `unanswered_empty` (an empty answer "
    "for no reason above) and `answered`; `unrecorded` marks an arm written before "
    "outcomes were recorded whose outcome could not be back-filled. The `outcome:` "
    "rows count each; `budget-exhausted rate` counts both exhausted outcomes, "
    "`answered-within-budget rate` counts `answered` alone, and `near cap` counts "
    "tasks within one turn of the budget."
)

# What the turn rows count; ``{penalty}`` is the plan's budget + 1.
_TURN_ROWS_READING = (
    "`turns-to-answer (penalised, exhausted = cap+1)` is the headline: an answered "
    "task counts its own turns, and every unanswered one — exhausted, finalized "
    "after exhaustion, starved, timed out or empty — counts the budget + 1 = "
    "{penalty} turns, since it never answered within the budget. `unrecorded` "
    "tasks drop out of both turn-to-answer means and stay in the tally. "
    "`turns-to-answer (answered only)` averages the answered tasks alone, and "
    "`turns (per task)` is the raw count of model replies, penalty-free. `turns "
    "after needle` counts the replies after the turn whose call first surfaced a "
    "gold file; the `calls after first gold` rows count the calls after that "
    "call, and the `... read` rows the calls after the first `read_file` or "
    "`get_symbol` that returned one."
)


# What each definition of a used call actually claims — stated in the report so a
# reader never has to infer which one produced the number.
_USED_DEFINITION_NOTES: Mapping[UsedCallDefinition, str] = {
    UsedCallDefinition.ATTRIBUTED_EVIDENCE: (
        "a call counts when a row it returned became part of the answer's attributed evidence."
    ),
    UsedCallDefinition.NOT_NEEDLESS: (
        "an answering run leaves no patch to attribute a row to, so a call counts when no "
        "needless-call component charged it — a weaker claim than attributed evidence."
    ),
}
