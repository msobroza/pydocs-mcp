"""The blind labeller request: the ground truth and the answer, never another judge's verdict."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pydocs_eval.judge.alignment_request import (
    AlignmentLabelError,
    labeller_request,
    labels_output,
    parse_labels,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabelConfidence, LabelVerdict, QuestionLabel
from pydocs_eval.judge.config import JevConfig

from ._alignment_fakes import (
    ASTRA_MODEL,
    REPOQA_REFERENCE,
    answered,
    chat_row,
    label,
    repoqa_context,
    repoqa_row,
)
from ._judge_fakes import golden

_JEV = JevConfig(model="jev-1.13")
# What the first labeller wrote: it must never reach the second one's request.
_OTHER_LABELLERS_EVIDENCE = "ASTRA-ONLY-EVIDENCE-SENTINEL"


def _row_with_astra_answered() -> AlignmentLabelRow:
    astra = answered(
        ASTRA_MODEL,
        {
            "needle_identified": label("false", evidence=_OTHER_LABELLERS_EVIDENCE),
            "addresses_grader": label("true", LabelConfidence.LOW, _OTHER_LABELLERS_EVIDENCE),
            "contradicts_reference": label("true", evidence=_OTHER_LABELLERS_EVIDENCE),
        },
    )
    return repoqa_row(astra=astra)


def _wire(request) -> dict[str, object]:
    return {
        "custom_id": request.custom_id,
        "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        "output": {"name": request.output.name, "schema": request.output.schema},
    }


def test_the_labeller_request_matches_its_golden() -> None:
    request = labeller_request(_row_with_astra_answered(), jev=_JEV)

    assert _wire(request) == golden("alignment_labeller_request.json")


def test_a_labeller_sees_the_gold_its_code_the_reference_and_the_answer() -> None:
    text = json.dumps(_wire(labeller_request(repoqa_row(), jev=_JEV)))

    for shown in (
        "sklearn/base.py",
        "BaseEstimator.get_params",
        "def get_params(self, deep=True):",
        "out = dict()",
        json.dumps(REPOQA_REFERENCE)[1:-1],
        "The parameters are read by",
    ):
        assert shown in text


def test_a_labeller_never_sees_the_other_label_or_why_the_item_was_drawn() -> None:
    row = _row_with_astra_answered()

    text = json.dumps(_wire(labeller_request(row, jev=_JEV)))

    assert _OTHER_LABELLERS_EVIDENCE not in text
    assert row.stratum.value not in text
    assert "probability" not in text


def test_a_chat_labeller_sees_every_gold_site_and_its_text() -> None:
    request = labeller_request(chat_row(), jev=_JEV)
    text = json.dumps(_wire(request))

    assert "class MaxSimStrategy:" in text
    assert "Scores are MaxSim over token embeddings." in text
    assert list(request.output.schema["properties"]) == list(chat_row().questions)


def test_each_question_asks_a_verdict_in_its_own_vocabulary_evidence_and_a_confidence() -> None:
    properties = labels_output(("site_0", "completeness")).schema["properties"]

    site, completeness = properties["site_0"], properties["completeness"]
    assert site["properties"]["verdict"]["enum"] == ["true", "false"]
    assert completeness["properties"]["verdict"]["enum"] == ["L0", "L1", "L2", "L3"]
    assert site["properties"]["confidence"]["enum"] == ["low", "medium", "high"]
    assert site["required"] == ["verdict", "evidence", "confidence"]


def test_only_the_named_questions_are_asked() -> None:
    request = labeller_request(repoqa_row(), jev=_JEV, question_ids=("addresses_grader",))

    assert list(request.output.schema["properties"]) == ["addresses_grader"]
    assert "needle_identified" not in request.messages[1].content


def test_a_question_the_item_does_not_have_is_refused() -> None:
    with pytest.raises(ValueError, match="completeness"):
        labeller_request(repoqa_row(), jev=_JEV, question_ids=("completeness",))


def test_an_answer_over_the_cap_is_never_sent_to_a_labeller() -> None:
    row = repoqa_row(context=repoqa_context(answer="x" * 12001))

    with pytest.raises(ValueError, match="12001"):
        labeller_request(row, jev=_JEV)


def test_a_labellers_answer_reads_as_one_label_per_question() -> None:
    content = {
        "needle_identified": {"verdict": "true", "evidence": "`get_params`", "confidence": "high"},
        "completeness": {"verdict": "L2", "evidence": "most of it", "confidence": "low"},
    }

    labels = parse_labels(content, ("needle_identified", "completeness"))

    assert labels == {
        "needle_identified": QuestionLabel(LabelVerdict.TRUE, "`get_params`", LabelConfidence.HIGH),
        "completeness": QuestionLabel(LabelVerdict.L2, "most of it", LabelConfidence.LOW),
    }


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        ({}, "needle_identified"),
        ({"needle_identified": "true"}, "needle_identified"),
        ({"needle_identified": {"verdict": "L2", "evidence": "", "confidence": "high"}}, "'L2'"),
        (
            {"needle_identified": {"verdict": "true", "evidence": "", "confidence": "sure"}},
            "'sure'",
        ),
        ({"needle_identified": {"verdict": "true", "evidence": 3, "confidence": "high"}}, "3"),
    ],
)
def test_an_answer_outside_the_schema_is_refused_naming_what_is_wrong(
    answer: dict[str, object], named: str
) -> None:
    with pytest.raises(AlignmentLabelError, match=named):
        parse_labels(answer, ("needle_identified",))


def test_the_request_is_the_same_whichever_labeller_already_answered() -> None:
    """Blind: the request is built from the item alone, so neither labeller's answer moves it."""
    assert labeller_request(_row_with_astra_answered(), jev=_JEV) == labeller_request(
        replace(repoqa_row()), jev=_JEV
    )
