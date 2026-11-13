"""What the annotation page says about an alignment item: one plain-text line per fact.

The page (``annotate_app``) only draws these lines, so their wording can be read
and changed without a browser, and this module stays free of Streamlit. Each is
plain text: the page shows the answers, the reference and every evidence line
verbatim, never as Markdown, because a planted injection must read exactly as
the judges read it. The lines share one reading of a row, where it stands with
the owner (:class:`OwnerQueueState`).

Example:
    >>> code_check_lines(CodeCheck(needle_cited=True, gold_site_coverage=0.5, verdicts={"site_0": True}))
    ('needle cited: yes', 'gold-site coverage: 50%', 'site_0: yes')
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydocs_eval.judge.alignment_report import owner_queue
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, CodeCheck, OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot
from pydocs_eval.judge.jev_requests import JudgedSite
from pydocs_eval.judge.reference_sources import ShownCode

_SEPARATOR = " · "


class OwnerQueueState(StrEnum):
    """Where an item stands with the owner; the value is how the item picker words it."""

    #: A reason sent it to the owner, and a question it holds has no owner verdict yet.
    AWAITING_RULING = "awaiting a ruling"
    #: A reason sent it to the owner, who has ruled on every question the reasons hold.
    RULED = "ruled"
    #: Both labellers answered and agreed on every question, and nothing escalated it.
    AGREED = "agreed"
    #: Both labellers answered, but no run has adjudicated their answers since.
    NOT_ADJUDICATED = "not adjudicated"
    #: A labeller has not answered yet (pending, still running, or failed).
    BEING_LABELLED = "being labelled"

    @property
    def queued(self) -> bool:
        """Whether the item is in the owner's queue."""
        return self in (OwnerQueueState.AWAITING_RULING, OwnerQueueState.RULED)


# Why an item is not in the owner's queue, by where it stands.
_NOT_QUEUED_BECAUSE = {
    OwnerQueueState.AGREED: "the labellers agreed and nothing escalated it",
    OwnerQueueState.NOT_ADJUDICATED: "not adjudicated since both labellers answered",
    OwnerQueueState.BEING_LABELLED: "the labellers have not both answered",
}


def owner_queue_state(row: AlignmentLabelRow) -> OwnerQueueState:
    """Where ``row`` stands with the owner.

    "Awaiting" is read through ``owner_queue``, so the page counts exactly what
    ``report-alignment`` counts.
    """
    if row.escalation_reasons:
        awaiting = owner_queue((row,)).awaiting
        return OwnerQueueState.AWAITING_RULING if awaiting else OwnerQueueState.RULED
    if not row.labelled_by_both:
        return OwnerQueueState.BEING_LABELLED
    if all(question_id in row.agreed for question_id in row.questions):
        return OwnerQueueState.AGREED
    return OwnerQueueState.NOT_ADJUDICATED


def item_identity_line(row: AlignmentLabelRow) -> str:
    """Where the item comes from: dataset, task, pass, stratum, arm, trajectory, planted injection."""
    parts = [
        row.dataset.value,
        row.task_id,
        row.alignment_pass.value,
        f"stratum {row.stratum.value}",
        f"arm {row.source.arm}",
        f"trajectory {row.source.trajectory_id}",
    ]
    if row.planted_injection:
        parts.append(f"planted injection {row.planted_injection}")
    return _SEPARATOR.join(parts)


def labeller_models_line(row: AlignmentLabelRow) -> str:
    """Which model answered as each labeller: ``Astra: openai/gpt-6-astra:batch · Opus: …``."""
    return _SEPARATOR.join(
        f"{_labeller_name(slot)}: {row.labeller(slot).model}" for slot in LabellerSlot
    )


def queue_reason_lines(row: AlignmentLabelRow) -> tuple[str, ...]:
    """Why the owner sees the item, one reason a line, or the one line saying why it is not queued."""
    state = owner_queue_state(row)
    if not state.queued:
        return (f"not in the owner's queue: {_NOT_QUEUED_BECAUSE[state]}",)
    return tuple(_escalation_reason_line(reason) for reason in row.escalation_reasons)


def gold_site_line(site: JudgedSite) -> str:
    """One gold site: its path, then its symbol and span when the dataset names them."""
    return _SEPARATOR.join(part for part in (site.path, site.symbol, site.span) if part)


def shown_code_caption(code: ShownCode) -> str:
    """Which code a block shows: the site, its gold span, and the lines shown around it."""
    span = f"gold lines {code.start}–{code.end}"
    shown = f"shown {code.first_line}–{code.last_line}"
    return _SEPARATOR.join(part for part in (code.site.path, code.site.symbol, span, shown) if part)


def code_check_lines(check: CodeCheck) -> tuple[str, ...]:
    """What the deterministic check says, before any judge: citation, coverage, each verdict."""
    return (
        f"needle cited: {_yes_no(check.needle_cited)}",
        f"gold-site coverage: {check.gold_site_coverage:.0%}",
        *(f"{question_id}: {_yes_no(cited)}" for question_id, cited in check.verdicts.items()),
    )


def labeller_answer_line(row: AlignmentLabelRow, slot: LabellerSlot, question_id: str) -> str:
    """One labeller on one question: ``Astra: true (high) — <evidence>``, or why it has no answer."""
    name = _labeller_name(slot)
    entry = row.labeller(slot)
    label = entry.labels.get(question_id)
    if label is None:
        why = f": {entry.reason}" if entry.reason else ""
        return f"{name}: no answer ({entry.status.value}{why})"
    return f"{name}: {label.verdict.value} ({label.confidence.value}) — {label.evidence}"


def queue_summary_line(rows: Sequence[AlignmentLabelRow]) -> str:
    """The file at a glance: its items, those in the owner's queue, those awaiting a ruling."""
    queue = owner_queue(rows)
    counts = (f"{len(rows)} items", f"{queue.items} in the owner queue")
    return _SEPARATOR.join((*counts, f"{queue.awaiting} awaiting a ruling"))


def item_option_label(row: AlignmentLabelRow) -> str:
    """How the item picker lists ``row``: its id first, its task, its reasons and where it stands."""
    reasons = dict.fromkeys(each.reason.value for each in row.escalation_reasons)
    return _SEPARATOR.join((row.item_id, row.task_id, *reasons, owner_queue_state(row).value))


def _labeller_name(slot: LabellerSlot) -> str:
    """The spec's name for a labeller slot: ``Astra``, ``Opus``."""
    return slot.value.capitalize()


def _escalation_reason_line(reason: OwnerEscalation) -> str:
    """One reason the owner sees the item: ``disagreement on site_0``, ``audit (whole item)``."""
    where = f"on {reason.question}" if reason.question else "(whole item)"
    return f"{reason.reason.value} {where}"


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


__all__ = (
    "OwnerQueueState",
    "code_check_lines",
    "gold_site_line",
    "item_identity_line",
    "item_option_label",
    "labeller_answer_line",
    "labeller_models_line",
    "owner_queue_state",
    "queue_reason_lines",
    "queue_summary_line",
    "shown_code_caption",
)
