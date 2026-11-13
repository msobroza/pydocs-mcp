"""The owner's ruling on one alignment item, written into the label file and nowhere else.

The annotation page records the owner's word through :func:`record_owner_ruling`.
It replaces the item's ``owner_decision`` and ``owner_note`` and rewrites the
file in one atomic write (``write_alignment_labels``); every other field of
every row keeps its bytes.

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


def owner_ruling_verdicts(question_id: str) -> tuple[LabelVerdict, ...]:
    """The verdicts the owner may give ``question_id``: the question's own, or ``undecided``.

    Example:
        >>> [verdict.value for verdict in owner_ruling_verdicts("needle_identified")]
        ['true', 'false', 'undecided']
    """
    return (*verdicts_for(question_id), LabelVerdict.UNDECIDED)


def record_owner_ruling(path: Path, ruling: OwnerRuling) -> AlignmentLabelRow:
    """Write ``ruling`` into its item's row of ``path``: only ``owner_decision`` and ``owner_note`` move.

    Raises:
        AlignmentLabelFileError: a line of ``path`` is not a row of its revision.
        ValueError: the item is not in ``path``, or the decision names a question
            not asked of it or a verdict outside the question's vocabulary;
            nothing is written.
        OSError: the rewrite failed; ``path`` keeps its bytes.
    """
    rows = read_alignment_labels(path)
    index = _row_index(rows, ruling.item_id, path)
    _refuse_unfit_decision(rows[index], ruling.decision)
    ruled = replace(rows[index], owner_decision=dict(ruling.decision), owner_note=ruling.note)
    write_alignment_labels(path, (*rows[:index], ruled, *rows[index + 1 :]))
    log.info(
        json.dumps(
            {"event": "owner_ruling_recorded", "item": ruling.item_id, "labels": str(path)},
            sort_keys=True,
        )
    )
    return ruled


def _row_index(rows: Sequence[AlignmentLabelRow], item_id: str, path: Path) -> int:
    for index, row in enumerate(rows):
        if row.item_id == item_id:
            return index
    raise ValueError(
        f"item {item_id!r} is not in {path.name}, expected one of its {len(rows)} items"
    )


def _refuse_unfit_decision(row: AlignmentLabelRow, decision: Mapping[str, LabelVerdict]) -> None:
    """Refuse a verdict on a question the item was not asked, or one its question cannot take."""
    for question_id, verdict in decision.items():
        if question_id not in row.questions:
            raise ValueError(
                f"question {question_id!r} was not asked of item {row.item_id!r}, "
                f"expected one of {list(row.questions)}"
            )
        allowed = owner_ruling_verdicts(question_id)
        if verdict not in allowed:
            raise ValueError(
                f"{question_id} = {verdict.value!r} on item {row.item_id!r}, "
                f"expected one of {[each.value for each in allowed]}"
            )


__all__ = ("OwnerRuling", "owner_ruling_verdicts", "record_owner_ruling")
