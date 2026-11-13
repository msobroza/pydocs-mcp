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
from dataclasses import dataclass, field, replace
from typing import NamedTuple

from pydocs_eval.judge.alignment_vocabulary import (
    AlignmentPass,
    AlignmentStratum,
    EscalationReason,
    LabellerSlot,
    LabelStatus,
    LabelVerdict,
    QuestionLabel,
)
from pydocs_eval.judge.jev_questions import JevQuestionKind, kind_of
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.reference_sources import ShownCode


class QuestionKey(NamedTuple):
    """One question of one dataset, as every per-question figure is keyed.

    A question is a kind: ``site_0`` and ``site_1`` are both ``site``, as Jev's
    threshold blocks are.
    """

    dataset: JudgedDataset
    question: JevQuestionKind

    @classmethod
    def of(cls, dataset: JudgedDataset, question_id: str) -> QuestionKey:
        """The key of ``question_id`` on ``dataset``.

        Example:
            >>> QuestionKey.of(JudgedDataset.EXAMPLE_NEEDLE_CHAT, "site_2").question
            <JevQuestionKind.SITE: 'site'>
        """
        return cls(dataset, kind_of(question_id))


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

    def contradicts(self, question_id: str, says_yes: bool | None) -> bool:
        """Whether a decided yes/no verdict on ``question_id`` disagrees with code's own.

        ``says_yes`` is ``None`` for no decided verdict, which contradicts nothing.
        """
        cited = self.verdicts.get(question_id)
        return cited is not None and says_yes is not None and says_yes != cited


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
    alignment_pass: AlignmentPass
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
        """The entry of the labeller in ``slot`` (its field is named for the slot)."""
        entry: LabellerEntry = getattr(self, slot.value)
        return entry

    def with_labeller(self, slot: LabellerSlot, entry: LabellerEntry) -> AlignmentLabelRow:
        """This row with ``slot``'s entry replaced by ``entry``."""
        if slot is LabellerSlot.ASTRA:
            return replace(self, astra=entry)
        return replace(self, opus=entry)

    @property
    def labelled_by_both(self) -> bool:
        """Whether both blind labellers answered the item."""
        return self.astra.answered and self.opus.answered

    def question_key(self, question_id: str) -> QuestionKey:
        """The per-question key ``question_id`` of this item is counted under."""
        return QuestionKey.of(self.dataset, question_id)


__all__ = (
    "AlignmentItemContext",
    "AlignmentLabelRow",
    "AnswerSource",
    "CodeCheck",
    "LabellerEntry",
    "OwnerEscalation",
    "QuestionKey",
)
