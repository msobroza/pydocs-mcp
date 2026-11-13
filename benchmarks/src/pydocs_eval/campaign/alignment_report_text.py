"""What ``report-alignment`` prints: the labels' agreement, the owner's queue, Luna's trust, the sweep.

Example:
    >>> print(render_alignment_report(rows, judge))  # doctest: +SKIP
    alignment labels: 100 items, 100 labelled by both
    ...
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from pydocs_eval.campaign.alignment_label_text import (
    agreement_figure_text,
    agreement_lines,
    queue_lines,
)
from pydocs_eval.judge.alignment_adjudication import audit_share
from pydocs_eval.judge.alignment_report import (
    audit_review,
    disagreement_directions,
    labeller_agreement,
    owner_queue,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, QuestionKey
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.escalation_trust import EscalationTrustRow, escalation_trust
from pydocs_eval.judge.threshold_sweep import SweepRow
from pydocs_eval.judge.thresholds import NoulBand

#: One question's sweep: its bands (yes/no) or its confidence gates (completeness).
SweepTable = Mapping[QuestionKey, Sequence[SweepRow]]


def render_alignment_report(
    rows: Sequence[AlignmentLabelRow], judge: JudgeConfig, *, sweep: SweepTable | None = None
) -> str:
    """Everything the label file says, plus the threshold sweep when one was run."""
    labelled = sum(1 for row in rows if row.astra.answered and row.opus.answered)
    lines = [f"alignment labels: {len(rows)} items, {labelled} labelled by both"]
    lines += agreement_lines(
        labeller_agreement(rows),
        disagreement_directions(rows),
        min_labeller_kappa=judge.alignment.min_labeller_kappa,
    )
    lines += queue_lines(owner_queue(rows))
    lines += _audit_lines(rows, judge)
    lines += _trust_lines(rows, judge)
    if sweep is not None:
        lines += _sweep_lines(sweep)
    return "\n".join(lines)


def _audit_lines(rows: Sequence[AlignmentLabelRow], judge: JudgeConfig) -> Iterator[str]:
    config = judge.alignment
    yield (
        f"audit share: {audit_share(rows, config):.2f} (base {config.agreed_audit_share:.2f}; "
        f"grows past {config.audit_growth_threshold:.0%} audited wrong)"
    )
    for review in audit_review(rows, config):
        flag = " — REVIEW ITS CRITERIA" if review.review_criteria else ""
        yield f"  {review.dataset} {review.question}: {review.wrong}/{review.ruled} audited labels wrong{flag}"


def _trust_lines(rows: Sequence[AlignmentLabelRow], judge: JudgeConfig) -> Iterator[str]:
    if not any(row.escalation_judge is not None for row in rows):
        yield "escalation judge: no agreement pass in this file yet"
        return
    scale = judge.escalation.floor_scale.value
    yield (
        f"escalation judge per question (floor min({judge.escalation.agreement_floor:.2f}, "
        f"labellers' kappa), compared on {scale}):"
    )
    yield from (_trust_line(row) for row in escalation_trust(rows, judge))


def _trust_line(row: EscalationTrustRow) -> str:
    text = agreement_figure_text
    verdict = "trusted" if row.trusted else "undecided (below the floor)"
    if row.labeller_kappa_low:
        verdict = "undecided (the labellers' kappa is low: rewrite its criteria first)"
    flag = "; KAPPA ABOVE THE LABELLERS'" if row.kappa_above_labellers else ""
    return (
        f"  {row.dataset} {row.question}: n={row.items} agreement {text(row.percent_agreement)} "
        f"kappa {text(row.kappa)} (labellers' kappa {text(row.labeller_kappa)}, floor "
        f"{text(row.floor)}); with astra {text(row.agreement_with_astra)}, with opus "
        f"{text(row.agreement_with_opus)}: {verdict}{flag}"
    )


def _sweep_lines(sweep: SweepTable) -> Iterator[str]:
    yield "threshold sweep (agreement against the share Jev decides alone):"
    for (dataset, question), table in sorted(sweep.items()):
        for row in table:
            yield f"  {dataset} {question} {_sweep_row(row)}"


def _sweep_row(row: SweepRow) -> str:
    threshold = row.threshold
    where = (
        f"band {threshold.low:.2f}/{threshold.high:.2f}"
        if isinstance(threshold, NoulBand)
        else f"min_confidence {threshold.min_confidence:.1f}"
    )
    return (
        f"{where}: decides {row.auto_decided}/{row.items} ({row.auto_decided_share:.0%}), "
        f"agreement {agreement_figure_text(row.agreement)}"
    )


__all__ = ("SweepTable", "render_alignment_report")
