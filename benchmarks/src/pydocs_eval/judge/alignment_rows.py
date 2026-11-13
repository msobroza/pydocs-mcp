"""One alignment item as the label file holds it: what was judged, by whom, and what it settled to.

An item is a real agent answer (or one with a planted injection) drawn from a
stored arm. Its ``context`` is everything a judge of it is shown — the ground
truth and the answer, never another judge's verdict. The two blind labellers'
answers sit under ``astra`` and ``opus``; ``agreed`` is what their agreement
settles each question to, ``escalation_reasons`` why the owner sees the item,
and ``owner_decision`` / ``owner_note`` what the owner wrote back. The escalation
judge's answers, from its agreement pass, sit under ``escalation_judge``.
``alignment_label_file`` reads and writes rows; ``alignment_adjudication``
fills ``agreed`` and ``escalation_reasons``.

Example:
    >>> LabellerEntry(model="openai/gpt-6-astra:batch").status
    <LabelStatus.PENDING: 'pending'>
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydocs_eval.judge.alignment_vocabulary import (
    AlignmentStratum,
    EscalationReason,
    LabellerSlot,
    LabelStatus,
    LabelVerdict,
    QuestionLabel,
)
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.reference_sources import ShownCode


@dataclass(frozen=True, slots=True)
class AlignmentItemContext:
    """What every judge of one item is shown: the question, the gold, its code, the reference, the answer.

    ``gold`` is the needle's sites (repoqa-qa: one path and its function; chat:
    every site with its span; swe-qa-questions: the gold files). ``shown_code``
    is the code at the gold — the needle's body with a few lines around it, or
    each chat site's span — and is empty where the gold names files only.
    """

    dataset: JudgedDataset
    question: str
    gold: tuple[JudgedSite, ...]
    shown_code: tuple[ShownCode, ...]
    reference_answer: str | None
    agent_answer: str


@dataclass(frozen=True, slots=True)
class AnswerSource:
    """Which stored run wrote the answer: its arm (``role@commit``) and trajectory."""

    arm: str
    trajectory_id: str


@dataclass(frozen=True, slots=True)
class CodeCheck:
    """What the deterministic check says of the answer, before any judge.

    ``verdicts`` is code's verdict on each gold question of the item
    (``needle_identified``, each ``site_<i>`` or ``gold_file_<i>``), the one a
    labeller's verdict is compared with.
    """

    needle_cited: bool
    gold_site_coverage: float
    verdicts: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class LabellerEntry:
    """One judge's answers to one item, and where asking it stands.

    ``batch_id`` names the batch it ran in (empty for a synchronous call);
    ``batch_cost_usd`` is that whole batch's reported cost, ``cost_usd`` a
    synchronous answer's own; ``reason`` says why a failed ask failed.
    """

    model: str
    status: LabelStatus = LabelStatus.PENDING
    labels: Mapping[str, QuestionLabel] = field(default_factory=dict)
    batch_id: str = ""
    batch_cost_usd: float | None = None
    cost_usd: float | None = None
    reason: str = ""

    @property
    def answered(self) -> bool:
        return self.status is LabelStatus.ANSWERED


@dataclass(frozen=True, slots=True)
class OwnerEscalation:
    """One reason the owner sees an item; ``question`` is empty when it concerns the whole item."""

    reason: EscalationReason
    question: str = ""


@dataclass(frozen=True, slots=True)
class AlignmentLabelRow:
    """One alignment item: what was judged, both labellers' answers, and how it settled."""

    item_id: str
    dataset: JudgedDataset
    jev_model: str
    task_id: str
    source: AnswerSource
    stratum: AlignmentStratum
    context: AlignmentItemContext
    questions: tuple[str, ...]
    code_check: CodeCheck
    astra: LabellerEntry
    opus: LabellerEntry
    planted_injection: str = ""
    agreed: Mapping[str, LabelVerdict] = field(default_factory=dict)
    escalation_reasons: tuple[OwnerEscalation, ...] = ()
    owner_decision: Mapping[str, LabelVerdict] = field(default_factory=dict)
    owner_note: str = ""
    escalation_judge: LabellerEntry | None = None

    def labeller(self, slot: LabellerSlot) -> LabellerEntry:
        """The entry of the labeller in ``slot``."""
        return self.astra if slot is LabellerSlot.ASTRA else self.opus


__all__ = (
    "AlignmentItemContext",
    "AlignmentLabelRow",
    "AnswerSource",
    "CodeCheck",
    "LabellerEntry",
    "OwnerEscalation",
)
