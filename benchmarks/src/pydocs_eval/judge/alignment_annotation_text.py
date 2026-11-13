"""What the annotation page says about an alignment item: one plain-text line per fact.

The page (``annotate_app``) only draws these lines, so their wording can be read
and changed without a browser, and this module stays free of Streamlit. Each is
plain text: the page shows the answers, the reference and every evidence line
verbatim, never as Markdown, because a planted injection must read exactly as
the judges read it.

Example:
    >>> code_check_lines(CodeCheck(needle_cited=True, gold_site_coverage=0.5, verdicts={"site_0": True}))
    ('needle cited: yes', 'gold-site coverage: 50%', 'site_0: yes')
"""

from __future__ import annotations

from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, CodeCheck, OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot
from pydocs_eval.judge.jev_requests import JudgedSite
from pydocs_eval.judge.reference_sources import ShownCode

_SEPARATOR = " · "


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


def labeller_name(slot: LabellerSlot) -> str:
    """The spec's name for a labeller slot: ``Astra``, ``Opus``."""
    return slot.value.capitalize()


def escalation_reason_line(reason: OwnerEscalation) -> str:
    """One reason the owner sees the item: ``disagreement on site_0``, ``audit (whole item)``."""
    where = f"on {reason.question}" if reason.question else "(whole item)"
    return f"{reason.reason.value} {where}"


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
    entry = row.labeller(slot)
    label = entry.labels.get(question_id)
    if label is None:
        why = f": {entry.reason}" if entry.reason else ""
        return f"{labeller_name(slot)}: no answer ({entry.status.value}{why})"
    return f"{labeller_name(slot)}: {label.verdict.value} ({label.confidence.value}) — {label.evidence}"


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


__all__ = (
    "code_check_lines",
    "escalation_reason_line",
    "gold_site_line",
    "item_identity_line",
    "labeller_answer_line",
    "labeller_name",
    "shown_code_caption",
)
