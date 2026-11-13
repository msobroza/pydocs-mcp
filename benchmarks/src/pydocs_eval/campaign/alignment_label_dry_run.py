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
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, verdicts_for
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient

# A dry run's synthetic price for each labeller's batch: inside the $0.09-0.20 per
# item the sub-budget assumed for a 10-item pilot, so the sizing reads plausibly.
DRY_RUN_BATCH_USD = 0.75
_DRY_RUN_EVIDENCE = "dry run: synthetic label, no model was asked"
# One question in five gets the second labeller's opposite verdict, one in ten a low confidence.
_DRY_RUN_DISAGREE_EVERY = 5
_DRY_RUN_LOW_EVERY = 10


def dry_run_clients(rows: Sequence[AlignmentLabelRow]) -> LabellerClients:
    """Offline labellers that answer every row with synthetic labels: a $0 rehearsal of a run."""
    scripts = {
        slot: {row.item_id: _synthetic_answer(row, slot) for row in rows} for slot in LabellerSlot
    }
    return LabellerClients(
        astra=FakeOpenRouterChatClient(
            scripts[LabellerSlot.ASTRA],
            rows[0].astra.model if rows else "",
            batch_prefix="dry_run_astra",
            batch_cost_usd=DRY_RUN_BATCH_USD,
        ),
        opus=FakeOpenRouterChatClient(
            scripts[LabellerSlot.OPUS],
            rows[0].opus.model if rows else "",
            batch_prefix="dry_run_opus",
            batch_cost_usd=DRY_RUN_BATCH_USD,
        ),
    )


def _synthetic_answer(row: AlignmentLabelRow, slot: LabellerSlot) -> dict[str, object]:
    answer: dict[str, object] = {}
    for question_id in row.questions:
        draw = int(hashlib.sha256(f"{row.item_id}:{question_id}".encode()).hexdigest(), 16)
        allowed = verdicts_for(question_id)
        index = draw % len(allowed)
        if slot is LabellerSlot.OPUS and draw % _DRY_RUN_DISAGREE_EVERY == 0:
            index = (index + 1) % len(allowed)
        low = (draw // 7) % _DRY_RUN_LOW_EVERY == 0
        answer[question_id] = {
            "verdict": allowed[index].value,
            "evidence": _DRY_RUN_EVIDENCE,
            "confidence": "low" if low else "high",
        }
    return answer


__all__ = ("DRY_RUN_BATCH_USD", "dry_run_clients")
