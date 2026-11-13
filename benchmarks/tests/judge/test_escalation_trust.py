"""Whether the escalation judge is trusted on a question: its agreement against min(0.90, κ_labellers)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_eval.judge.alignment_adjudication import adjudicate
from pydocs_eval.judge.alignment_rows import CodeCheck
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot
from pydocs_eval.judge.escalation_trust import (
    escalation_floor,
    escalation_trust,
    meets_floor,
)
from pydocs_eval.judge.role_config import AlignmentConfig, EscalationConfig, EscalationFloorScale

from ._alignment_fakes import answered, labelled, labels_of, repoqa_row

_LUNA = "openai/gpt-6-luna"


@pytest.mark.parametrize(
    ("labeller_kappa", "floor"),
    [(None, 0.90), (0.75, 0.75), (0.95, 0.90), (0.0, None), (-0.2, None)],
)
def test_the_floor_is_the_agreement_floor_or_the_labellers_kappa_where_lower(
    labeller_kappa: float | None, floor: float | None
) -> None:
    """At or below chance (κ <= 0) the labellers settle nothing, so no floor can be met."""
    assert escalation_floor(0.90, labeller_kappa) == floor


@pytest.mark.parametrize(
    ("scale", "met"),
    [(EscalationFloorScale.PERCENT_AGREEMENT, True), (EscalationFloorScale.KAPPA, False)],
)
def test_the_floor_scale_decides_which_of_lunas_figures_meets_it(
    scale: EscalationFloorScale, met: bool
) -> None:
    assert meets_floor(percent_agreement=0.8, kappa=0.3, floor=0.75, scale=scale) is met


def test_no_figure_and_no_floor_meet_nothing() -> None:
    percent = EscalationFloorScale.PERCENT_AGREEMENT
    assert not meets_floor(percent_agreement=None, kappa=None, floor=0.5, scale=percent)
    assert not meets_floor(percent_agreement=1.0, kappa=1.0, floor=None, scale=percent)


def _item(index: int, *, labellers: tuple[str, str], luna: str, question: str):
    """A repoqa item both labellers and Luna answered; only ``question`` varies between items."""
    good = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}
    astra = labels_of(**{**good, question: labellers[0]})
    opus = labels_of(**{**good, question: labellers[1]})
    cited = (question != "needle_identified") or labellers[0] == "true"
    row = labelled(repoqa_row(f"{question}_{index}"), astra, opus)
    row = replace(
        row,
        code_check=CodeCheck(cited, float(cited), {"needle_identified": cited}),
        escalation_judge=answered(_LUNA, labels_of(**{**good, question: luna})),
    )
    return row


def _needle_items():
    # Labellers agree on all ten (8 true, 2 false): κ 1.0. Luna misses one true: 9/10.
    specs = [("true", "true", "true")] * 7 + [("true", "true", "false")]
    specs += [("false", "false", "false")] * 2
    return [
        _item(i, labellers=(a, o), luna=luna, question="needle_identified")
        for i, (a, o, luna) in enumerate(specs)
    ]


def _reference_items():
    # Labellers agree on 8 (4 + 4) and split twice: κ 0.6. Luna sides with Astra every time.
    specs = [("true", "true", "true")] * 4 + [("false", "false", "false")] * 4
    specs += [("true", "false", "true"), ("false", "true", "false")]
    return [
        _item(i, labellers=(a, o), luna=luna, question="contradicts_reference")
        for i, (a, o, luna) in enumerate(specs)
    ]


def _trust(rows, config: EscalationConfig = EscalationConfig()):
    settled = adjudicate(
        rows,
        config=AlignmentConfig(agreed_audit_share=0.0),
        self_preferring=LabellerSlot.OPUS,
    )
    return {row.question: row for row in escalation_trust(settled, config)}


def test_luna_is_trusted_on_a_question_whose_agreement_reaches_the_floor() -> None:
    needle = _trust(_needle_items())["needle_identified"]

    assert (needle.items, needle.percent_agreement) == (10, 0.9)
    assert needle.labeller_kappa == pytest.approx(1.0)
    assert needle.floor == pytest.approx(0.90)
    assert needle.trusted
    # p_o 0.9, p_e (7*8 + 3*2)/100 = 0.62: κ = 0.28/0.38.
    assert needle.kappa == pytest.approx(0.28 / 0.38)
    assert not needle.kappa_above_labellers


def test_a_low_labeller_kappa_lowers_the_floor_and_lunas_higher_kappa_is_flagged() -> None:
    reference = _trust(_reference_items())["contradicts_reference"]

    assert reference.labeller_kappa == pytest.approx(0.6)
    assert reference.floor == pytest.approx(0.6)
    # Luna is compared only with the 8 labels that stand, and agrees with all of them.
    assert (reference.items, reference.percent_agreement, reference.kappa) == (8, 1.0, 1.0)
    assert reference.trusted
    assert reference.kappa_above_labellers


def test_lunas_agreement_with_each_labeller_alone_is_reported_beside() -> None:
    reference = _trust(_reference_items())["contradicts_reference"]

    assert reference.agreement_with_astra == pytest.approx(1.0)
    assert reference.agreement_with_opus == pytest.approx(0.8)


def test_below_the_floor_luna_is_not_trusted() -> None:
    rows = _needle_items()
    rows[0] = _item(0, labellers=("true", "true"), luna="false", question="needle_identified")

    needle = _trust(rows)["needle_identified"]

    assert needle.percent_agreement == pytest.approx(0.8)
    assert not needle.trusted


def test_under_the_kappa_scale_lunas_own_kappa_must_reach_the_floor() -> None:
    kappa_scale = EscalationConfig(floor_scale=EscalationFloorScale.KAPPA)

    needle = _trust(_needle_items(), kappa_scale)["needle_identified"]

    assert not needle.trusted


def test_a_question_luna_never_labelled_is_not_trusted() -> None:
    rows = [replace(row, escalation_judge=None) for row in _needle_items()]

    needle = _trust(rows)["needle_identified"]

    assert (needle.items, needle.percent_agreement, needle.trusted) == (0, None, False)
