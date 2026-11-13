"""Labelling the alignment set: both blind labellers, batches journaled in the label file, nothing bought twice."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pydocs_eval.judge.alignment_labelling import (
    EndedBatch,
    LabellerClients,
    LabellingResult,
    label_alignment_rows,
    label_with_escalation_judge,
    labelling_cost,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelStatus, LabelVerdict
from pydocs_eval.judge.chat_wire import ChatFailureKind
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient

from ._alignment_fakes import ASTRA_MODEL, OPUS_MODEL, labelled, labels_of, repoqa_row

_JEV = JevConfig(model="jev-1.13")
_GOOD = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}


def _content(**changes: str) -> dict[str, object]:
    verdicts = {**_GOOD, **changes}
    return {
        qid: {"verdict": verdict, "evidence": f"{qid} line", "confidence": "high"}
        for qid, verdict in verdicts.items()
    }


def _clients(astra_script, opus_script, *, cost: float | None = 0.5) -> LabellerClients:
    return LabellerClients(
        astra=FakeOpenRouterChatClient(
            astra_script, ASTRA_MODEL, batch_prefix="astra", batch_cost_usd=cost
        ),
        opus=FakeOpenRouterChatClient(
            opus_script, OPUS_MODEL, batch_prefix="opus", batch_cost_usd=cost
        ),
    )


class _Snapshots:
    """Every state of the rows the run asked to persist."""

    def __init__(self) -> None:
        self.saved: list[tuple[AlignmentLabelRow, ...]] = []

    def __call__(self, rows: Sequence[AlignmentLabelRow]) -> None:
        self.saved.append(tuple(rows))


def _label(rows, clients, persist=None, **options) -> LabellingResult:
    return label_alignment_rows(rows, clients, jev=_JEV, persist=persist or _Snapshots(), **options)


def test_both_labellers_answer_every_row_and_each_answer_is_recorded() -> None:
    rows = [repoqa_row("a"), repoqa_row("b")]
    clients = _clients(
        {"a": _content(), "b": _content(needle_identified="false")},
        {"a": _content(), "b": _content()},
    )

    result = _label(rows, clients)

    a, b = result.rows
    assert a.astra.status is a.opus.status is LabelStatus.ANSWERED
    assert b.astra.labels["needle_identified"].verdict is LabelVerdict.FALSE
    assert b.astra.labels["needle_identified"].evidence == "needle_identified line"
    assert (a.astra.batch_id, a.opus.batch_id) == ("astra_1", "opus_1")
    assert (result.asked, result.collected) == (4, 0)


def test_each_batch_id_is_persisted_before_any_answer_is_read() -> None:
    persist = _Snapshots()

    _label([repoqa_row("a")], _clients({"a": _content()}, {"a": _content()}), persist)

    first = persist.saved[0][0]
    assert (first.astra.status, first.astra.batch_id) == (LabelStatus.SUBMITTED, "astra_1")
    assert first.astra.labels == {}


def test_a_row_still_running_is_collected_by_its_batch_id_and_never_resubmitted() -> None:
    clients = _clients({"a": [ChatFailureKind.STILL_RUNNING, _content()]}, {"a": _content()})
    first = _label([repoqa_row("a")], clients)

    second = _label(first.rows, clients)

    assert first.rows[0].astra.status is LabelStatus.SUBMITTED
    assert second.rows[0].astra.status is LabelStatus.ANSWERED
    assert [batch for batch, _ in clients.astra.batches] == ["astra_1"]
    assert (second.asked, second.collected) == (0, 1)


def test_an_unusable_answer_fails_the_row_and_the_next_run_asks_it_again() -> None:
    clients = _clients({"a": [{"needle_identified": "yes"}, _content()]}, {"a": _content()})

    first = _label([repoqa_row("a")], clients)
    second = _label(first.rows, clients)

    assert first.rows[0].astra.status is LabelStatus.FAILED
    assert "needle_identified" in first.rows[0].astra.reason
    assert second.rows[0].astra.status is LabelStatus.ANSWERED


def test_a_failed_submit_is_asked_again_only_on_request() -> None:
    clients = _clients({"a": [ChatFailureKind.NOT_SUBMITTED, _content()]}, {"a": _content()})
    first = _label([repoqa_row("a")], clients)

    untouched = _label(first.rows, clients)
    resubmitted = _label(first.rows, clients, resubmit_not_submitted=True)

    assert first.rows[0].astra.status is LabelStatus.NOT_SUBMITTED
    assert untouched.rows[0].astra.status is LabelStatus.NOT_SUBMITTED
    assert resubmitted.rows[0].astra.status is LabelStatus.ANSWERED


def test_a_row_both_labellers_answered_is_never_asked_again() -> None:
    answered_row = labelled(repoqa_row("a"), labels_of(**_GOOD), labels_of(**_GOOD))
    clients = _clients({}, {})

    result = _label([answered_row], clients)

    assert result.rows == (answered_row,)
    assert clients.astra.requests == clients.opus.requests == []


def test_ended_batches_are_named_for_deletion_and_a_running_one_is_not() -> None:
    clients = _clients(
        {"a": ChatFailureKind.STILL_RUNNING, "b": _content()},
        {"a": _content(), "b": _content()},
    )

    result = _label([repoqa_row("a"), repoqa_row("b")], clients)

    assert set(result.ended_batches) == {EndedBatch(LabellerSlot.OPUS, "opus_1")}


def test_the_labelling_cost_counts_each_batch_once_per_item() -> None:
    clients = _clients(
        {"a": _content(), "b": _content()}, {"a": _content(), "b": _content()}, cost=0.6
    )
    result = _label([repoqa_row("a"), repoqa_row("b")], clients)

    cost = labelling_cost(result.rows)

    assert cost.total_usd == pytest.approx(1.2)
    assert cost.items == 2
    assert cost.per_item_usd == pytest.approx(0.6)
    assert cost.unpriced_batches == 0


def test_a_batch_with_no_reported_cost_is_counted_unpriced() -> None:
    clients = _clients({"a": _content()}, {"a": _content()}, cost=None)

    cost = labelling_cost(_label([repoqa_row("a")], clients).rows)

    assert (cost.total_usd, cost.unpriced_batches) == (0.0, 2)


def test_the_escalation_judges_agreement_pass_labels_every_labelled_row_once() -> None:
    labelled_row = labelled(repoqa_row("a"), labels_of(**_GOOD), labels_of(**_GOOD))
    waiting = repoqa_row("b")
    luna = FakeOpenRouterChatClient({"a": _content(needle_identified="false")})

    first = label_with_escalation_judge(
        [labelled_row, waiting], luna, model="openai/gpt-6-luna", jev=_JEV
    )
    again = label_with_escalation_judge(first, luna, model="openai/gpt-6-luna", jev=_JEV)

    judged, untouched = first
    assert judged.escalation_judge is not None
    assert judged.escalation_judge.labels["needle_identified"].verdict is LabelVerdict.FALSE
    assert judged.escalation_judge.model == "openai/gpt-6-luna"
    assert untouched.escalation_judge is None
    assert again == first
    assert len(luna.requests) == 1
