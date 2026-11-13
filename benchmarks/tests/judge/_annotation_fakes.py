"""A label file as a labelling run leaves it, for the annotation page to open.

The rows go through #381's own path — ``label_alignment_rows`` with two
``FakeOpenRouterChatClient`` labellers, then ``adjudicate`` — and are written by
``write_alignment_labels``, so the page reads exactly what a ``label-alignment``
run writes. Three items: one the labellers disagree on, one agreed with a
low-confidence label, and one cleanly agreed (its audit draw, 0.19, is above the
0.10 share, so no reason sends it to the owner).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path

from pydocs_eval.judge.alignment_adjudication import adjudicate
from pydocs_eval.judge.alignment_label_file import write_alignment_labels
from pydocs_eval.judge.alignment_labelling import LabellerClients, label_alignment_rows
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
from pydocs_eval.judge.role_config import AlignmentConfig

from ._alignment_fakes import ASTRA_MODEL, JEV_MODEL, OPUS_MODEL, chat_row, repoqa_row

#: Astra says the answer names the needle, Opus says it does not.
DISAGREED_ITEM = "item_disagreed"
#: Both agree on every question, but Opus is unsure of the completeness level.
LOW_CONFIDENCE_ITEM = "item_low_confidence"
#: Both agree on every question, sure of each: nothing sends it to the owner.
CLEAN_ITEM = "item_clean"

_REPOQA_AGREED = {
    "needle_identified": "true",
    "addresses_grader": "false",
    "contradicts_reference": "false",
}
_CHAT_AGREED = {
    "site_0": "true",
    "site_1": "false",
    "addresses_grader": "false",
    "completeness": "L2",
    "contradicts_reference": "false",
}
_SCRIPTS: dict[LabellerSlot, dict[str, dict[str, str]]] = {
    LabellerSlot.ASTRA: {
        DISAGREED_ITEM: _REPOQA_AGREED,
        LOW_CONFIDENCE_ITEM: _CHAT_AGREED,
        CLEAN_ITEM: _REPOQA_AGREED,
    },
    LabellerSlot.OPUS: {
        DISAGREED_ITEM: {**_REPOQA_AGREED, "needle_identified": "false"},
        LOW_CONFIDENCE_ITEM: _CHAT_AGREED,
        CLEAN_ITEM: _REPOQA_AGREED,
    },
}
# (labeller, item) -> the questions it answers with low confidence.
_LOW: dict[tuple[LabellerSlot, str], frozenset[str]] = {
    (LabellerSlot.OPUS, LOW_CONFIDENCE_ITEM): frozenset({"completeness"}),
}


def evidence_line(slot: LabellerSlot, item_id: str, question_id: str) -> str:
    """The evidence line ``slot`` gave for ``question_id`` of ``item_id``: unique per answer."""
    return f"{slot.value} quotes {item_id}/{question_id}: `get_params` returns a dict"


def write_labelled_file(path: Path) -> tuple[AlignmentLabelRow, ...]:
    """Label the three items with fake-client labellers, adjudicate them and write ``path``."""
    rows = (repoqa_row(DISAGREED_ITEM), chat_row(LOW_CONFIDENCE_ITEM), repoqa_row(CLEAN_ITEM))
    labelled = label_alignment_rows(
        rows,
        _clients(),
        jev=JevConfig(model=JEV_MODEL),
        persist=lambda persisted: write_alignment_labels(path, persisted),
    )
    settled = adjudicate(labelled.rows, config=AlignmentConfig(), self_preferring=LabellerSlot.OPUS)
    write_alignment_labels(path, settled)
    return settled


def _clients() -> LabellerClients:
    models = {LabellerSlot.ASTRA: ASTRA_MODEL, LabellerSlot.OPUS: OPUS_MODEL}
    clients = {
        slot: FakeOpenRouterChatClient(
            {item_id: _answer(slot, item_id, verdicts) for item_id, verdicts in script.items()},
            models[slot],
            batch_prefix=slot.value,
            batch_cost_usd=0.5,
        )
        for slot, script in _SCRIPTS.items()
    }
    return LabellerClients(astra=clients[LabellerSlot.ASTRA], opus=clients[LabellerSlot.OPUS])


def _answer(slot: LabellerSlot, item_id: str, verdicts: Mapping[str, str]) -> dict[str, object]:
    low: Collection[str] = _LOW.get((slot, item_id), frozenset())
    return {
        question_id: {
            "verdict": verdict,
            "evidence": evidence_line(slot, item_id, question_id),
            "confidence": "low" if question_id in low else "high",
        }
        for question_id, verdict in verdicts.items()
    }
