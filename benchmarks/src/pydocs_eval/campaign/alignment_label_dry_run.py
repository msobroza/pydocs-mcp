"""A ``label-alignment`` dry run: offline labellers that answer with synthetic labels, at $0.

The rehearsal exercises everything a paid run does — the draw, the label file
written before any spend, the batch journal, both labellers' answers, the
adjudication, the report and the sizing — with two
:class:`~pydocs_eval.judge.openrouter_chat.FakeOpenRouterChatClient` labellers
in place of OpenRouter. Their verdicts are drawn from a hash of the item and
the question, the second labeller differing on about one question in five, and
their batches are priced at a synthetic :data:`DRY_RUN_BATCH_USD` each. Nothing
in a dry run's label file is a real label.

Example:
    >>> clients = dry_run_clients(plan.rows)  # doctest: +SKIP
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydocs_eval.judge.alignment_labelling import LabellerClients
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabelConfidence, LabellerSlot, verdicts_for
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient

# A dry run's synthetic price for each labeller's batch: inside the $0.09-0.20 per
# item the sub-budget assumed for a 10-item pilot, so the sizing reads plausibly.
DRY_RUN_BATCH_USD = 0.75
_DRY_RUN_EVIDENCE = "dry run: synthetic label, no model was asked"
# The second labeller flips about one verdict in five; one label in ten is low-confidence.
_DRY_RUN_DISAGREE_EVERY = 5
_DRY_RUN_LOW_EVERY = 10


def dry_run_clients(rows: Sequence[AlignmentLabelRow]) -> LabellerClients:
    """Offline labellers that answer every row with synthetic labels: a $0 rehearsal of a run."""
    clients = {
        slot: FakeOpenRouterChatClient(
            {row.item_id: _synthetic_answer(row, slot) for row in rows},
            rows[0].labeller(slot).model if rows else "",
            batch_prefix=f"dry_run_{slot.value}",
            batch_cost_usd=DRY_RUN_BATCH_USD,
        )
        for slot in LabellerSlot
    }
    return LabellerClients(astra=clients[LabellerSlot.ASTRA], opus=clients[LabellerSlot.OPUS])


def _synthetic_answer(row: AlignmentLabelRow, slot: LabellerSlot) -> dict[str, object]:
    return {qid: _synthetic_label(row.item_id, qid, slot) for qid in row.questions}


def _synthetic_label(item_id: str, question_id: str, slot: LabellerSlot) -> dict[str, str]:
    """A verdict, a confidence and a disagreement, each read off its own byte of a hash."""
    verdict_byte, disagree_byte, confidence_byte = hashlib.sha256(
        f"{item_id}:{question_id}".encode()
    ).digest()[:3]
    allowed = verdicts_for(question_id)
    index = verdict_byte % len(allowed)
    if slot is LabellerSlot.OPUS and disagree_byte % _DRY_RUN_DISAGREE_EVERY == 0:
        index = (index + 1) % len(allowed)
    low = confidence_byte % _DRY_RUN_LOW_EVERY == 0
    confidence = LabelConfidence.LOW if low else LabelConfidence.HIGH
    return {
        "verdict": allowed[index].value,
        "evidence": _DRY_RUN_EVIDENCE,
        "confidence": confidence.value,
    }


__all__ = ("DRY_RUN_BATCH_USD", "dry_run_clients")
