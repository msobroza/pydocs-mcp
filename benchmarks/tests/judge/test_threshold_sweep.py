"""The threshold sweep: agreement against auto-decided share, band by band, gate by gate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_eval.judge.alignment_vocabulary import LabelVerdict
from pydocs_eval.judge.config import JevConfig, JudgeConfig
from pydocs_eval.judge.jev_client import FakeJevJudgeClient
from pydocs_eval.judge.jev_requests import JudgedAnswer, JudgedSite, repoqa_request_plan
from pydocs_eval.judge.jev_scoring import calibration_answers
from pydocs_eval.judge.jev_wire import JevResponse, NoulAnswer, ScoreAnswer
from pydocs_eval.judge.judge_errors import JudgeModelMismatchError
from pydocs_eval.judge.thresholds import CompletenessLevel, NoulBand, ScoreGate
from pydocs_eval.judge.threshold_sweep import (
    first_band_reaching,
    sweep_noul_bands,
    sweep_pairs,
    sweep_score_gates,
)

from ._alignment_fakes import labelled, labels_of, repoqa_row

_PAIRS = [
    (0.9, True),
    (0.8, True),
    (0.75, False),
    (0.65, False),
    (0.5, True),
    (0.35, False),
    (0.1, False),
]


def test_the_band_is_swept_from_its_start_outward() -> None:
    rows = sweep_noul_bands(_PAIRS, band_start=(0.3, 0.7))

    assert [(row.band.low, row.band.high) for row in rows] == [
        (0.3, 0.7),
        (0.25, 0.75),
        (0.2, 0.8),
        (0.15, 0.85),
        (0.1, 0.9),
        (0.05, 0.95),
        (0.0, 1.0),
    ]


def test_each_band_reports_its_agreement_against_its_auto_decided_share() -> None:
    start, wider = sweep_noul_bands(_PAIRS, band_start=(0.3, 0.7))[::2][:2]

    # At 0.3/0.7 Jev decides 0.9, 0.8, 0.75 (yes) and 0.1 (no): 3 of the 4 agree.
    assert (start.items, start.auto_decided, start.agreeing) == (7, 4, 3)
    assert start.auto_decided_share == pytest.approx(4 / 7)
    assert start.agreement == pytest.approx(0.75)
    # At 0.2/0.8 the 0.75 answer is in the band: 3 decided, all 3 agree.
    assert (wider.auto_decided, wider.agreement) == (3, 1.0)


def test_a_band_that_decides_nothing_has_no_agreement_figure() -> None:
    (row,) = [r for r in sweep_noul_bands([(0.5, True)], band_start=(0.3, 0.7)) if r.band.low == 0.3]

    assert (row.auto_decided, row.auto_decided_share, row.agreement) == (0, 0.0, None)


def test_the_first_band_reaching_a_target_decides_the_most() -> None:
    rows = sweep_noul_bands(_PAIRS, band_start=(0.3, 0.7))

    # 0.75 still reads yes at 0.25/0.75 (a probability at the edge is decided).
    assert first_band_reaching(rows, 0.9) == NoulBand(low=0.2, high=0.8)
    assert first_band_reaching(rows, 0.7) == NoulBand(low=0.3, high=0.7)
    assert first_band_reaching(sweep_noul_bands([(0.9, False)], band_start=(0.3, 0.7)), 0.5) is None


def _score(score: float, confidence: float) -> ScoreAnswer:
    return ScoreAnswer(score=score, confidence=confidence, probabilities={})


def test_a_score_is_cut_at_the_level_midpoints_and_compared_as_a_level_never_averaged() -> None:
    pairs = [
        (_score(1.5, 0.9), CompletenessLevel.L2),
        (_score(1.49, 0.9), CompletenessLevel.L1),
        (_score(2.6, 0.9), CompletenessLevel.L3),
        (_score(1.6, 0.9), CompletenessLevel.L1),
    ]

    (gate0,) = [row for row in sweep_score_gates(pairs) if row.gate.min_confidence == 0.0]

    # 1.5 -> L2, 1.49 -> L1, 2.6 -> L3 agree; 1.6 -> L2 is not the label L1.
    assert (gate0.auto_decided, gate0.agreeing) == (4, 3)


def test_each_confidence_gate_reports_its_agreement_against_its_auto_decided_share() -> None:
    pairs = [
        (_score(3.0, 0.95), CompletenessLevel.L3),
        (_score(0.2, 0.4), CompletenessLevel.L2),
        (_score(2.2, 0.75), CompletenessLevel.L2),
    ]

    rows = {row.gate.min_confidence: row for row in sweep_score_gates(pairs)}

    assert [round(gate, 1) for gate in rows] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    assert (rows[0.0].auto_decided, rows[0.0].agreement) == (3, pytest.approx(2 / 3))
    assert (rows[0.5].auto_decided, rows[0.5].agreement) == (2, 1.0)
    assert (rows[0.9].auto_decided, rows[0.9].auto_decided_share) == (1, pytest.approx(1 / 3))
    assert rows[0.9].gate == ScoreGate(min_confidence=0.9)


_GOOD = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}


def test_the_sweep_pairs_jevs_answers_with_the_labels_that_stand() -> None:
    agreed = labelled(repoqa_row("a"), labels_of(**_GOOD), labels_of(**_GOOD))
    rows = [
        replace(agreed, agreed={qid: LabelVerdict(v) for qid, v in _GOOD.items()}),
        replace(agreed, item_id="b", agreed={"needle_identified": LabelVerdict.UNDECIDED}),
    ]
    answers = {
        "a": {"needle_identified": NoulAnswer(0.97), "addresses_grader": NoulAnswer(0.02)},
        "b": {"needle_identified": NoulAnswer(0.5)},
    }

    pairs = sweep_pairs(rows, answers)

    assert pairs.nouls[("repoqa-qa", "needle_identified")] == [(0.97, True)]
    assert pairs.nouls[("repoqa-qa", "addresses_grader")] == [(0.02, False)]
    assert ("repoqa-qa", "contradicts_reference") not in pairs.nouls
    assert pairs.scores == {}


_JEV = JevConfig(model="jev-1.13")
_SERVED = "typesafe/jev-1.13-20260917"


def _plan():
    judged = JudgedAnswer("Where?", "It is `get_params`.", "ref")
    return repoqa_request_plan(judged, JudgedSite("sklearn/base.py", "get_params"), jev=_JEV)


def test_calibration_reads_jevs_probabilities_without_any_threshold() -> None:
    plan = _plan()
    answers = {qid: NoulAnswer(0.6) for qid in plan.question_ids}
    judge = FakeJevJudgeClient.answering([(plan.requests[0], JevResponse(_SERVED, answers))])

    read = calibration_answers(plan, judge=judge, config=JudgeConfig(jev=_JEV))

    assert read == answers


def test_calibration_leaves_an_outage_unread_and_refuses_another_model() -> None:
    plan = _plan()
    outage = calibration_answers(
        plan, judge=FakeJevJudgeClient(scripted={}), config=JudgeConfig(jev=_JEV)
    )
    other = FakeJevJudgeClient.answering([(plan.requests[0], JevResponse("typesafe/jev-2.0", {}))])

    assert outage == {}
    with pytest.raises(JudgeModelMismatchError):
        calibration_answers(plan, judge=other, config=JudgeConfig(jev=_JEV))
