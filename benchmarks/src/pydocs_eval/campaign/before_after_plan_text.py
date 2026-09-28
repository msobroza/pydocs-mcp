"""The plan's words: what a before/after run would do, and what it is estimated to cost.

Rendering only, kept apart from the plan it renders (``before_after``) the way
``before_after_report_text`` is kept apart from the report. Nothing here
decides anything: every number arrives on the :class:`MeasurementPlan`, and
every assumption behind the estimate is printed beside it.
"""

from __future__ import annotations

from pydocs_eval.campaign.before_after import (
    REPORTED_METRICS,
    REPORTED_STATISTICS,
    SHORT_SHA_CHARS,
    CommitUnderTest,
    CostModel,
    MeasurementPlan,
)


def render_plan(plan: MeasurementPlan) -> str:
    """The plan-only output: what would run, and what it is estimated to cost.

    Example:
        >>> print(render_plan(plan))  # doctest: +SKIP
        before/after measurement plan (NOTHING HAS BEEN SPENT)
    """
    return "\n".join(
        [
            "before/after measurement plan (NOTHING HAS BEEN SPENT)",
            "",
            *_plan_scope_lines(plan),
            "",
            *_plan_estimate_lines(plan),
            "",
            "metrics reported for both arms:",
            *(f"  - {name}" for name in REPORTED_METRICS),
            "",
            f"reported as: {REPORTED_STATISTICS}",
            "",
            "re-run with --confirm-spend to execute both arms.",
        ]
    )


def _plan_scope_lines(plan: MeasurementPlan) -> list[str]:
    """What the run covers: split, arms, endpoint, budget."""
    return [
        f"split:      {plan.split} — {len(plan.task_ids)} task(s)",
        f"baseline:   {_commit_line(plan.baseline)}",
        f"candidate:  {_commit_line(plan.candidate)}",
        f"model:      {plan.model} @ {plan.endpoint}",
        *_workspace_lines(plan),
        f"turns:      {plan.max_agent_turns} agent turn(s) per task (the harness budget)",
        *(plan.llm_block.plan_lines() if plan.llm_block is not None else []),
        *(acceptance.plan_line() for acceptance in plan.arm_blocks),
    ]


def _workspace_lines(plan: MeasurementPlan) -> list[str]:
    """What ``--workspace`` means for this split, and what each task will search.

    A task that names a corpus searches a bundle built for THAT corpus under the
    ``--workspace`` directory; a task that names none searches ``--workspace``
    itself, which is what every task did before per-corpus workspaces existed.
    """
    shared = len(plan.task_workspaces.shared_task_ids)
    return [
        f"workspace:  {plan.workspace} — the root the per-corpus workspaces are built "
        f"under; searched directly by the {shared} task(s) that name no corpus",
        *plan.task_workspaces.preflight_lines(),
    ]


def _commit_line(commit: CommitUnderTest) -> str:
    short = commit.sha[:SHORT_SHA_CHARS]
    return f"{short}  {commit.subject}  ({commit.description_tokens} description tokens)"


def _plan_estimate_lines(plan: MeasurementPlan) -> list[str]:
    """The estimate and, beneath it, every assumption it rests on."""
    turn_estimate, turn_assumption = _turn_wording(plan)
    return [
        f"estimate:   {plan.rollouts} rollout(s), {turn_estimate}, ~{plan.tool_calls} tool call(s)",
        f"            ~{plan.input_tokens} input + ~{plan.output_tokens} output tokens",
        f"            ~${plan.estimated_usd:.2f}{_price_note(plan.cost)}",
        "assumptions (none of these is measured):",
        turn_assumption,
        f"  - {plan.cost.calls_per_turn} tool call(s) per tool-calling turn",
        f"  - {plan.cost.context_tokens_per_turn} context tokens per turn, plus that "
        "arm's description surface",
        f"  - {plan.cost.output_tokens_per_turn} output tokens per turn",
    ]


def _turn_wording(plan: MeasurementPlan) -> tuple[str, str]:
    """The estimate's turn count and the assumption behind it, as the plan prints them."""
    budget = plan.max_agent_turns
    if plan.cost.expected_turns_per_rollout is None:
        return (
            f"up to {plan.model_turns} model turn(s)",
            f"  - every rollout spends its full {budget}-turn budget",
        )
    return (
        f"~{plan.model_turns} model turn(s)",
        f"  - each rollout spends ~{plan.turns_per_rollout:g} of its {budget}-turn budget "
        "(--expected-turns-per-rollout)",
    )


def _price_note(cost: CostModel) -> str:
    """Say so when the dollar figure is zero because no price was supplied."""
    if cost.usd_per_1m_input or cost.usd_per_1m_output:
        return f" at ${cost.usd_per_1m_input}/1M input, ${cost.usd_per_1m_output}/1M output"
    return " — no price given; pass --usd-per-1m-input / --usd-per-1m-output"


__all__ = ("render_plan",)
