"""What ``label-alignment`` prints: its plan before any spend, and what a run found.

Example:
    >>> print(render_plan(plan, judge))  # doctest: +SKIP
    label-alignment repoqa-qa/small_dev (pilot)
      items in the label file: 0; items to draw: 10 (target 10)
    ...
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Sequence

from pydocs_eval.campaign.alignment_label_run import AlignmentLabelPlan, AlignmentLabelReport
from pydocs_eval.judge.alignment_report import DisagreementDirection, OwnerQueue, QuestionAgreement
from pydocs_eval.judge.alignment_sizing import ASSUMED_ITEM_USD, AlignmentSizing
from pydocs_eval.judge.alignment_vocabulary import AlignmentStratum, LabellerSlot, LabelStatus
from pydocs_eval.judge.config import JudgeConfig

# A rough English-text ratio: the plan prints an order of magnitude, not a bill.
_CHARS_PER_TOKEN = 4
_DRY_RUN_BANNER = "DRY RUN — offline labellers, synthetic labels and costs; no model was asked."


def render_plan(plan: AlignmentLabelPlan, judge: JudgeConfig) -> str:
    """The plan as the owner reads it before any spend."""
    request = plan.request
    strata = Counter(item.stratum for item in plan.drawn)
    low, high = ASSUMED_ITEM_USD
    labellers = judge.alignment.labellers
    return "\n".join(
        [
            f"label-alignment {request.split} ({request.alignment_pass.value})",
            f"  items in the label file: {len(plan.existing)}; items to draw: {len(plan.drawn)} "
            f"(target {request.target_items})",
            f"  drawn by stratum: {_strata(strata)}",
            f"  pool: {len(plan.pool.candidates)} stored answers; skipped: {_skipped(plan)}",
            f"  reserved questions never drawn: {len(request.reserved)} "
            f"(excluded from this pool: {plan.pool.excluded_reserved})",
            f"  Jev first pass: {'yes' if plan.jev_first_pass else 'no (borderline and code-vs-Jev strata stay empty)'}",
            *(
                f"  {slot.value}: {block.model} at {block.reasoning_effort.value}, "
                f"batch waited on {block.timeout_seconds:g} s"
                for slot, block in zip(LabellerSlot, labellers, strict=True)
            ),
            f"  prompts still to send: {plan.prompt_chars} chars (about "
            f"{plan.prompt_chars // _CHARS_PER_TOKEN} tokens), plus each answer's reasoning",
            f"  estimate at ${low:.2f}-{high:.2f} per dual-labelled item: "
            f"${low * len(plan.drawn):.2f}-{high * len(plan.drawn):.2f} for the items to draw",
            f"  items without a reference answer (contradicts_reference not asked): "
            f"{sum(1 for row in plan.rows if row.context.reference_answer is None)}",
            f"  label file: {request.labels_path}",
        ]
    )


def render_report(report: AlignmentLabelReport, *, dry_run: bool) -> str:
    """What the run did and what the labels say: cost, κ, directions, the owner's queue, sizing."""
    lines = [_DRY_RUN_BANNER] if dry_run else []
    lines += list(_status_lines(report))
    lines += list(agreement_lines(report.agreement, report.directions))
    lines += list(queue_lines(report.queue))
    if report.sizing is not None:
        lines += list(_sizing_lines(report.sizing))
    return "\n".join(lines)


def _strata(strata: Counter[AlignmentStratum]) -> str:
    return ", ".join(f"{stratum.value} {strata[stratum]}" for stratum in AlignmentStratum)


def _skipped(plan: AlignmentLabelPlan) -> str:
    return (
        ", ".join(f"{reason} {count}" for reason, count in sorted(plan.skipped.items())) or "none"
    )


def _status_lines(report: AlignmentLabelReport) -> Iterator[str]:
    statuses = Counter(row.labeller(slot).status for row in report.rows for slot in LabellerSlot)
    cost = report.cost
    per_item = (
        f"${cost.per_item_usd:.4f} per item" if cost.per_item_usd is not None else "n/a per item"
    )
    yield f"items: {len(report.rows)}; asked {report.labelling.asked}, collected {report.labelling.collected}"
    yield "labeller answers: " + ", ".join(
        f"{status.value} {statuses[status]}" for status in LabelStatus if statuses[status]
    )
    yield (
        f"cost reported by OpenRouter: ${cost.total_usd:.4f} over {cost.items} items = {per_item} "
        f"({cost.unpriced_batches} batches unpriced)"
    )
    yield f"batches deleted: {', '.join(report.deleted) or 'none'}"
    yield from (f"  - not deleted {batch_id}: {why}" for batch_id, why in report.not_deleted)


def agreement_lines(
    agreement: Sequence[QuestionAgreement], directions: Sequence[DisagreementDirection]
) -> Iterator[str]:
    """The labellers' κ per question, then which way each question's disagreements went."""
    yield "labellers' agreement per question (n, raw agreement, Cohen's kappa):"
    for row in agreement:
        yield (
            f"  {row.dataset} {row.question}: n={row.items} agreement {figure(row.percent_agreement)} "
            f"kappa {figure(row.kappa)}"
        )
    yield "disagreement directions (which labeller favoured the answer):"
    for row in directions:
        yield (
            f"  {row.dataset} {row.question}: {row.disagreements} disagreements, astra favours "
            f"{row.astra_favours_answer}, opus favours {row.opus_favours_answer}"
        )


def queue_lines(queue: OwnerQueue) -> Iterator[str]:
    """The items the owner sees, by reason, and how many still await a ruling."""
    reasons = ", ".join(
        f"{reason.value} {count}" for reason, count in sorted(queue.by_reason.items())
    )
    yield f"owner queue: {queue.items} items ({reasons or 'none'}); {queue.awaiting} awaiting a ruling"


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
        f"  pass 1: {sizing.pass_1_items} items ({sizing.pilot_items} from the pilot); "
        f"pass 2: {sizing.pass_2_items} items"
    )


def figure(value: float | None) -> str:
    """A rate or a κ to three places; ``n/a`` when undefined."""
    return "n/a" if value is None else f"{value:.3f}"


__all__ = ("agreement_lines", "figure", "queue_lines", "render_plan", "render_report")
