"""The alignment report: the labellers' κ per question, the disagreement directions, the owner's queue."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_eval.judge.alignment_adjudication import adjudicate
from pydocs_eval.judge.alignment_report import (
    audit_review,
    disagreement_directions,
    labeller_agreement,
    owner_queue,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import EscalationReason, LabellerSlot, LabelVerdict
from pydocs_eval.judge.role_config import AlignmentConfig

from ._alignment_fakes import chat_row, labelled, labels_of, repoqa_row

_GOOD = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}


def _repoqa(item_id: str, astra: dict[str, str], opus: dict[str, str]) -> AlignmentLabelRow:
    return labelled(
        repoqa_row(item_id), labels_of(**{**_GOOD, **astra}), labels_of(**{**_GOOD, **opus})
    )


def _four_repoqa_items() -> list[AlignmentLabelRow]:
    # needle_identified pairs: (T,T) (T,T) (F,F) (T,F) -> p_o 3/4, p_e 1/2, κ 0.5.
    return [
        _repoqa("a", {}, {}),
        _repoqa("b", {}, {}),
        _repoqa("c", {"needle_identified": "false"}, {"needle_identified": "false"}),
        _repoqa("d", {}, {"needle_identified": "false"}),
    ]


def test_the_labellers_kappa_is_reported_per_question() -> None:
    rows = labeller_agreement(_four_repoqa_items())

    by_question = {(row.dataset, row.question): row for row in rows}
    needle = by_question[("repoqa-qa", "needle_identified")]
    assert (needle.items, needle.percent_agreement) == (4, 0.75)
    assert needle.kappa == pytest.approx(0.5)
    grader = by_question[("repoqa-qa", "addresses_grader")]
    assert (grader.percent_agreement, grader.kappa) == (1.0, None)


def test_every_site_question_of_a_dataset_pools_into_one_question() -> None:
    rest = {"addresses_grader": "false", "completeness": "L2", "contradicts_reference": "false"}
    astra = labels_of(site_0="true", site_1="false", **rest)
    opus = labels_of(site_0="true", site_1="true", **rest)

    rows = labeller_agreement([labelled(chat_row(), astra, opus)])

    site = next(row for row in rows if row.question == "site")
    assert (site.dataset, site.items, site.percent_agreement) == ("example-needle-chat", 2, 0.5)


def test_an_item_a_labeller_has_not_answered_counts_nowhere() -> None:
    assert labeller_agreement([repoqa_row()]) == ()


def test_the_direction_of_every_disagreement_is_tabled_per_question() -> None:
    rows = [
        _repoqa("a", {"contradicts_reference": "true"}, {}),
        _repoqa("b", {"contradicts_reference": "true"}, {}),
        _repoqa("c", {}, {"contradicts_reference": "true"}),
        _repoqa("d", {"needle_identified": "false"}, {}),
        _repoqa("e", {}, {}),
    ]

    table = {row.question: row for row in disagreement_directions(rows)}

    reference = table["contradicts_reference"]
    assert (reference.disagreements, reference.opus_favours_answer) == (3, 2)
    assert reference.astra_favours_answer == 1
    needle = table["needle_identified"]
    assert (needle.disagreements, needle.astra_favours_answer, needle.opus_favours_answer) == (
        1,
        0,
        1,
    )
    assert table["addresses_grader"].disagreements == 0


def test_a_completeness_disagreement_is_read_by_level() -> None:
    rest = {"site_0": "true", "site_1": "false", "addresses_grader": "false"}
    rest |= {"contradicts_reference": "false"}
    row = labelled(
        chat_row(), labels_of(completeness="L1", **rest), labels_of(completeness="L3", **rest)
    )

    (completeness,) = (
        each for each in disagreement_directions([row]) if each.question == "completeness"
    )

    assert (completeness.opus_favours_answer, completeness.astra_favours_answer) == (1, 0)


def test_the_owner_queue_counts_items_by_reason_and_those_still_awaiting_a_ruling() -> None:
    settled = adjudicate(
        _four_repoqa_items(),
        config=AlignmentConfig(agreed_audit_share=0.0),
        self_preferring=LabellerSlot.OPUS,
    )
    decided = [
        replace(row, owner_decision={"needle_identified": LabelVerdict.TRUE})
        if row.item_id == "d"
        else row
        for row in settled
    ]

    queue = owner_queue(decided)

    # c: both say false against the code check; d: a disagreement, since ruled.
    assert queue.items == 2
    assert queue.by_reason == {EscalationReason.CODE_CHECK: 1, EscalationReason.DISAGREEMENT: 1}
    assert queue.awaiting == 1


def test_a_question_whose_audit_finds_more_than_5_percent_wrong_is_flagged_for_review() -> None:
    audited = []
    for i in range(10):
        verdict = LabelVerdict.FALSE if i == 0 else LabelVerdict.TRUE
        row = replace(
            _repoqa(f"item_{i}", {}, {}),
            agreed={qid: LabelVerdict(v) for qid, v in _GOOD.items()},
            escalation_reasons=(OwnerEscalation(EscalationReason.AUDIT),),
            owner_decision={"needle_identified": verdict, "addresses_grader": LabelVerdict.FALSE},
        )
        audited.append(row)

    review = {row.question: row for row in audit_review(audited, AlignmentConfig())}

    assert (review["needle_identified"].ruled, review["needle_identified"].wrong) == (10, 1)
    assert review["needle_identified"].review_criteria
    assert not review["addresses_grader"].review_criteria
