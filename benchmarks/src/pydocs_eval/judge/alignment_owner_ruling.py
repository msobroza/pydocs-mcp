"""The owner's ruling on one alignment item, written into the label file and nowhere else.

The annotation page records the owner's word through :func:`record_owner_ruling`.
It replaces the item's ``owner_decision`` and ``owner_note`` and rewrites the
file in one atomic write (``write_alignment_labels``); every other field of
every row keeps its bytes.

The owner rules with a question's own verdicts (``verdicts_for``: true/false, or
a level) or leaves it unruled; ``undecided`` is not an owner verdict here. The
audit reads any owner verdict unlike the agreed one as the labellers being
wrong (``alignment_adjudication.overruled_questions``), so a "cannot decide" on
an audited item would grow the audit share; an unruled question already reads
``undecided`` wherever a reason holds it.

Example:
    >>> ruling = OwnerRuling("item_1", {"needle_identified": LabelVerdict.FALSE}, "names no class")
    >>> record_owner_ruling(path, ruling).owner_note  # doctest: +SKIP
    'names no class'
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabelVerdict, verdicts_for

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OwnerRuling:
    """The owner's word on one item: a verdict on each question ruled on, and a free-text note."""

    item_id: str
    decision: Mapping[str, LabelVerdict] = field(default_factory=dict)
    note: str = ""


def record_owner_ruling(path: Path, ruling: OwnerRuling) -> AlignmentLabelRow:
    """Write ``ruling`` into its item's row of ``path``: only ``owner_decision`` and ``owner_note`` move.

    The file is read again here, so rows another run wrote since the page read it are kept.

    Raises:
        AlignmentLabelFileError: a line of ``path`` is not a row of its revision.
        ValueError: the item is not in ``path``, or the decision does not fit it
            (:func:`refuse_unfit_owner_decision`); nothing is written.
        OSError: the rewrite failed; ``path`` keeps its bytes.
    """
    rows = read_alignment_labels(path)
    index = _row_index(rows, ruling.item_id, path)
    refuse_unfit_owner_decision(rows[index], ruling.decision)
    ruled = replace(rows[index], owner_decision=dict(ruling.decision), owner_note=ruling.note)
    write_alignment_labels(path, (*rows[:index], ruled, *rows[index + 1 :]))
    log.info(
        json.dumps(
            {"event": "owner_ruling_recorded", "item": ruling.item_id, "labels": str(path)},
            sort_keys=True,
        )
    )
    return ruled


def refuse_unfit_owner_decision(
    row: AlignmentLabelRow, decision: Mapping[str, LabelVerdict]
) -> None:
    """Refuse a verdict on a question ``row`` was not asked, or one its question cannot take.

    Raises:
        ValueError: naming the item, the question and the verdict.
    """
    for question_id, verdict in decision.items():
        if question_id not in row.questions:
            raise ValueError(
                f"question {question_id!r} was not asked of item {row.item_id!r}, "
                f"expected one of {list(row.questions)}"
            )
        allowed = verdicts_for(question_id)
        if verdict not in allowed:
            raise ValueError(
                f"{question_id} = {verdict.value!r} on item {row.item_id!r}, "
                f"expected one of {[each.value for each in allowed]}"
            )


def _row_index(rows: Sequence[AlignmentLabelRow], item_id: str, path: Path) -> int:
    for index, row in enumerate(rows):
        if row.item_id == item_id:
            return index
    raise ValueError(
        f"item {item_id!r} is not in {path.name}, expected one of its {len(rows)} items"
    )


__all__ = ("OwnerRuling", "record_owner_ruling", "refuse_unfit_owner_decision")
