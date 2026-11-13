"""What ``label-alignment`` prints: its plan before any spend, and what a run found.

Example:
    >>> print(render_plan(plan, judge))  # doctest: +SKIP
    label-alignment repoqa-qa/small_dev (pilot)
      items of this pass in the label file: 0; items to draw: 10 (pass size 10)
    ...
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence

from pydocs_eval.campaign.alignment_label_run import AlignmentLabelPlan, AlignmentLabelReport
from pydocs_eval.judge.alignment_report import (
    DisagreementDirection,
    OwnerQueue,
    QuestionAgreement,
    is_low_kappa,
)
from pydocs_eval.judge.alignment_sizing import ASSUMED_ITEM_USD, AlignmentSizing
from pydocs_eval.judge.alignment_vocabulary import AlignmentStratum, LabellerSlot, LabelStatus
from pydocs_eval.judge.config import JudgeConfig

# A rough English-text ratio: the plan prints an order of magnitude, not a bill.
_CHARS_PER_TOKEN = 4
_DRY_RUN_BANNER = "DRY RUN — offline labellers, synthetic labels and costs; no model was asked."
_LOW_KAPPA_FLAG = " — LOW KAPPA: rewrite its criteria before any judge is trusted on it"


def render_plan(plan: AlignmentLabelPlan, judge: JudgeConfig) -> str:
    """The plan as the owner reads it before any spend."""
    request = plan.request
    lines = [f"label-alignment {request.split} ({request.alignment_pass.value})"]
    lines += _plan_draw_lines(plan)
    lines += _plan_labeller_lines(plan, judge)
    lines.append(f"  label file: {request.labels_path}")
    return "\n".join(lines)


def render_report(report: AlignmentLabelReport, judge: JudgeConfig, *, dry_run: bool) -> str:
    """What the run did and what the labels say: cost, κ, directions, the owner's queue, sizing."""
    lines = [_DRY_RUN_BANNER] if dry_run else []
    lines += _status_lines(report)
    lines += agreement_lines(
        report.agreement,
        report.directions,
        min_labeller_kappa=judge.alignment.min_labeller_kappa,
    )
    lines += queue_lines(report.queue)
    if report.sizing is not None:
        lines += _sizing_lines(report.sizing)
    return "\n".join(lines)


def agreement_lines(
    agreement: Sequence[QuestionAgreement],
    directions: Sequence[DisagreementDirection],
    *,
    min_labeller_kappa: float,
) -> Iterator[str]:
    """The labellers' κ per question (low ones flagged), then which way disagreements went."""
    yield "labellers' agreement per question (n, raw agreement, Cohen's kappa):"
    for row in agreement:
        flag = _LOW_KAPPA_FLAG if is_low_kappa(row.kappa, min_labeller_kappa) else ""
        yield (
            f"  {row.dataset} {row.question}: n={row.items} agreement "
            f"{agreement_figure_text(row.percent_agreement)} kappa "
            f"{agreement_figure_text(row.kappa)}{flag}"
        )
    yield "disagreement directions (which labeller favoured the answer):"
    for direction in directions:
        yield (
            f"  {direction.dataset} {direction.question}: {direction.disagreements} "
            f"disagreements, astra favours {direction.astra_favours_answer}, opus favours "
            f"{direction.opus_favours_answer}"
        )


def queue_lines(queue: OwnerQueue) -> Iterator[str]:
    """The items the owner sees, by reason, and how many still await a ruling."""
    reasons = ", ".join(
        f"{reason.value} {count}" for reason, count in sorted(queue.by_reason.items())
    )
    yield f"owner queue: {queue.items} items ({reasons or 'none'}); {queue.awaiting} awaiting a ruling"


def agreement_figure_text(value: float | None) -> str:
    """A rate or a κ to three places; ``n/a`` when undefined."""
    return "n/a" if value is None else f"{value:.3f}"


def _plan_draw_lines(plan: AlignmentLabelPlan) -> Iterator[str]:
    request = plan.request
    strata = Counter(item.stratum for item in plan.drawn)
    skipped = ", ".join(f"{reason.value} {count}" for reason, count in sorted(plan.skipped.items()))
    first_pass = (
        "yes" if plan.jev_first_pass else "no (borderline and code-vs-Jev strata stay empty)"
    )
    yield (
        f"  items of this pass in the label file: {plan.pass_items_held}; items to draw: "
        f"{len(plan.drawn)} (pass size {request.target_items}; {len(plan.existing)} items in the file)"
    )
    yield "  drawn by stratum: " + ", ".join(
        f"{stratum.value} {strata[stratum]}" for stratum in AlignmentStratum
    )
    yield f"  pool: {len(plan.pool.candidates)} stored answers; skipped: {skipped or 'none'}"
    yield (
        f"  reserved questions never drawn: {len(request.reserved)} "
        f"(excluded from this pool: {plan.pool.excluded_reserved})"
    )
    yield f"  Jev first pass: {first_pass}"


def _plan_labeller_lines(plan: AlignmentLabelPlan, judge: JudgeConfig) -> Iterator[str]:
    low, high = ASSUMED_ITEM_USD
    drawn = len(plan.drawn)
    for slot, block in zip(LabellerSlot, judge.alignment.labellers, strict=True):
        yield (
            f"  {slot.value}: {block.model} at {block.reasoning_effort.value}, batch waited on "
            f"{block.timeout_seconds:g} s"
        )
    yield (
        f"  prompts still to send: {plan.prompt_chars} chars (about "
        f"{plan.prompt_chars // _CHARS_PER_TOKEN} tokens), plus each answer's reasoning"
    )
    yield (
        f"  estimate at ${low:.2f}-{high:.2f} per dual-labelled item: "
        f"${low * drawn:.2f}-{high * drawn:.2f} for the items to draw"
    )
    without = sum(1 for row in plan.rows if row.context.reference_answer is None)
    yield f"  items without a reference answer (contradicts_reference not asked): {without}"


def _status_lines(report: AlignmentLabelReport) -> Iterator[str]:
    statuses = Counter(row.labeller(slot).status for row in report.rows for slot in LabellerSlot)
    cost, labelling = report.cost, report.labelling
    per_item = (
        f"${cost.per_item_usd:.4f} per item" if cost.per_item_usd is not None else "n/a per item"
    )
    yield f"items: {len(report.rows)}; asked {labelling.asked}, collected {labelling.collected}"
    yield "labeller answers: " + ", ".join(
        f"{status.value} {statuses[status]}" for status in LabelStatus if statuses[status]
    )
    yield (
        f"cost reported by OpenRouter: ${cost.total_usd:.4f} over {cost.items} items = {per_item} "
        f"({cost.unpriced_batches} batches unpriced)"
    )
    yield f"batches deleted: {', '.join(report.deleted) or 'none'}"
    yield from (f"  - not deleted {each.batch_id}: {each.reason}" for each in report.not_deleted)


def _sizing_lines(sizing: AlignmentSizing) -> Iterator[str]:
    if sizing.per_item_usd is None or sizing.affordable_items is None:
        yield "sizing: not possible — the pilot has no complete reported price"
        return
    band = "inside" if sizing.within_assumed_band else "outside"
    low, high = ASSUMED_ITEM_USD
    yield (
        f"sizing: ${sizing.per_item_usd:.4f} per item is {band} the ${low:.2f}-{high:.2f} the "
        f"sub-budget assumed; ${sizing.remaining_usd:.2f} left buys {sizing.affordable_items} items"
    )
    yield (
        f"  pass 1: {sizing.pass_1_items} items; pass 2: {sizing.pass_2_items} items "
        f"(beside the pilot's {sizing.pilot_items})"
    )


__all__ = (
    "agreement_figure_text",
    "agreement_lines",
    "queue_lines",
    "render_plan",
    "render_report",
)
