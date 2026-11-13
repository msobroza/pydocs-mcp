"""What ``report-alignment`` prints: the labels' agreement, the owner's queue, Luna's trust, the sweep.

Example:
    >>> print(render_alignment_report(rows, judge))  # doctest: +SKIP
    alignment labels: 100 items, 100 labelled by both
    ...
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from pydocs_eval.campaign.alignment_label_text import agreement_lines, figure, queue_lines
from pydocs_eval.judge.alignment_adjudication import audit_share
from pydocs_eval.judge.alignment_report import (
    QuestionKey,
    audit_review,
    disagreement_directions,
    labeller_agreement,
    owner_queue,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.escalation_trust import EscalationTrustRow, escalation_trust
from pydocs_eval.judge.threshold_sweep import BandSweepRow, GateSweepRow

#: One question's sweep: its bands (yes/no) or its confidence gates (completeness).
SweepTable = Mapping[QuestionKey, Sequence[BandSweepRow] | Sequence[GateSweepRow]]


def render_alignment_report(
    rows: Sequence[AlignmentLabelRow], judge: JudgeConfig, *, sweep: SweepTable | None = None
) -> str:
    """Everything the label file says, plus the threshold sweep when one was run."""
    labelled = sum(1 for row in rows if row.astra.answered and row.opus.answered)
    lines = [f"alignment labels: {len(rows)} items, {labelled} labelled by both"]
    lines += agreement_lines(labeller_agreement(rows), disagreement_directions(rows))
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
    yield from (_trust_line(row) for row in escalation_trust(rows, judge.escalation))


def _trust_line(row: EscalationTrustRow) -> str:
    verdict = "trusted" if row.trusted else "undecided (below the floor)"
    flag = "; KAPPA ABOVE THE LABELLERS'" if row.kappa_above_labellers else ""
    return (
        f"  {row.dataset} {row.question}: n={row.items} agreement {figure(row.percent_agreement)} "
        f"kappa {figure(row.kappa)} (labellers' kappa {figure(row.labeller_kappa)}, floor "
        f"{figure(row.floor)}); with astra {figure(row.agreement_with_astra)}, with opus "
        f"{figure(row.agreement_with_opus)}: {verdict}{flag}"
    )


def _sweep_lines(sweep: SweepTable) -> Iterator[str]:
    yield "threshold sweep (agreement against the share Jev decides alone):"
    for (dataset, question), table in sorted(sweep.items()):
        for row in table:
            yield f"  {dataset} {question} {_sweep_row(row)}"


def _sweep_row(row: BandSweepRow | GateSweepRow) -> str:
    where = (
        f"band {row.band.low:.2f}/{row.band.high:.2f}"
        if isinstance(row, BandSweepRow)
        else f"min_confidence {row.gate.min_confidence:.1f}"
    )
    return (
        f"{where}: decides {row.auto_decided}/{row.items} ({row.auto_decided_share:.0%}), "
        f"agreement {figure(row.agreement)}"
    )


__all__ = ("SweepTable", "render_alignment_report")
