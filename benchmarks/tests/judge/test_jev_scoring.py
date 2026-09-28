"""Scoring an answer with Jev: thresholds or a refusal, verdicts or ``undefined``, never 0."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from pydocs_eval.judge.config import JevConfig, JudgeConfig
from pydocs_eval.judge.jev_client import FakeJevJudgeClient
from pydocs_eval.judge.jev_questions import COMPLETENESS, NEEDLE_IDENTIFIED
from pydocs_eval.judge.jev_requests import (
    JevRequestPlan,
    JudgedAnswer,
    JudgedDataset,
    JudgedSite,
    chat_request_plan,
    repoqa_audit_request,
    repoqa_request_plan,
    swe_qa_request_plan,
)
from pydocs_eval.judge.jev_scoring import (
    AGREEMENT,
    CompletenessLevel,
    JevVerdict,
    score_answer,
)
from pydocs_eval.judge.jev_wire import JevAnswer, JevRequest, JevResponse, NoulAnswer, ScoreAnswer
from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeResponseError,
)
from pydocs_eval.judge.planted_injections import (
    PlantedInjection,
    load_planted_injections,
    planted_answer,
)
from pydocs_eval.judge.thresholds import MissingThresholdsError, NoulBand, ScoreGate

_JEV = JevConfig(model="jev-1.13")
_SERVED = "typesafe/jev-1.13-20260917"
_BAND = {"low": 0.3, "high": 0.7}
_NOUL_KINDS = (
    "needle_identified",
    "addresses_grader",
    "contradicts_reference",
    "site",
    "gold_file",
)


def _table(dataset: str = "repoqa-qa", *, drop: str = "") -> dict[str, object]:
    blocks: dict[str, object] = {kind: _BAND for kind in _NOUL_KINDS if kind != drop}
    if drop != "completeness":
        blocks["completeness"] = {"min_confidence": 0.6}
    return {"jev-1.13": {dataset: blocks}}


def _config(thresholds: Mapping[str, object] | None = None) -> JudgeConfig:
    return JudgeConfig.model_validate(
        {"jev": _JEV.model_dump(), "thresholds": _table() if thresholds is None else thresholds}
    )


def _judged(
    answer: str = "It is `get_params` in `sklearn/base.py`.", reference: str | None = "ref"
) -> JudgedAnswer:
    return JudgedAnswer(question="Where are parameters read?", answer=answer, reference=reference)


def _repoqa(judged: JudgedAnswer | None = None) -> JevRequestPlan:
    return repoqa_request_plan(
        judged or _judged(), JudgedSite("sklearn/base.py", "BaseEstimator.get_params"), jev=_JEV
    )


def _chat(site_count: int = 2, judged: JudgedAnswer | None = None) -> JevRequestPlan:
    sites = tuple(
        JudgedSite(f"src/m{i}.py", f"f{i}", f"src/m{i}.py:1-2") for i in range(site_count)
    )
    return chat_request_plan(judged or _judged(), sites, jev=_JEV)


def _swe_qa(judged: JudgedAnswer | None = None) -> JevRequestPlan:
    return swe_qa_request_plan(judged or _judged(), (JudgedSite("pkg/a.py"),), jev=_JEV)


def _answering(plan: JevRequestPlan, answers: Mapping[str, JevAnswer]) -> FakeJevJudgeClient:
    """A fake Jev answering each request of ``plan`` from ``answers``, by question id."""
    return FakeJevJudgeClient.answering(
        (request, JevResponse(_SERVED, {qid: answers[qid] for qid in request.questions}))
        for request in plan.requests
    )


def _nouls(probability: float, plan: JevRequestPlan) -> dict[str, JevAnswer]:
    answers: dict[str, JevAnswer] = {qid: NoulAnswer(probability) for qid in plan.question_ids}
    if "completeness" in answers:
        answers["completeness"] = ScoreAnswer(score=2.0, confidence=0.9, probabilities={"2": 0.9})
    return answers


@pytest.mark.parametrize(
    ("table", "block"),
    [
        ({}, "judge.thresholds.jev-1.13"),
        ({"jev-1.12": {"repoqa-qa": {}}}, "judge.thresholds.jev-1.13"),
        (_table("example-needle-chat"), "judge.thresholds.jev-1.13.repoqa-qa"),
        (_table(drop="addresses_grader"), "judge.thresholds.jev-1.13.repoqa-qa.addresses_grader"),
        (
            _table(drop="contradicts_reference"),
            "judge.thresholds.jev-1.13.repoqa-qa.contradicts_reference",
        ),
    ],
)
def test_score_refuses_naming_the_missing_thresholds_block_before_any_call(
    table: dict[str, object], block: str
) -> None:
    judge = FakeJevJudgeClient(scripted={})

    with pytest.raises(MissingThresholdsError) as refused:
        score_answer(_repoqa(), judge=judge, config=_config(table))

    assert f"{block} is missing" in str(refused.value)
    assert judge.calls == 0


def test_a_chat_score_needs_the_site_and_completeness_blocks() -> None:
    table = _table("example-needle-chat", drop="completeness")

    with pytest.raises(
        MissingThresholdsError, match=r"example-needle-chat\.completeness is missing"
    ):
        score_answer(_chat(), judge=FakeJevJudgeClient(scripted={}), config=_config(table))


def test_a_question_not_asked_needs_no_block() -> None:
    plan = _repoqa(_judged(reference=None))

    score = score_answer(
        plan,
        judge=_answering(plan, _nouls(0.9, plan)),
        config=_config(_table(drop="contradicts_reference")),
    )

    assert score.verdicts[AGREEMENT] is JevVerdict.UNDEFINED


def test_a_threshold_block_of_the_wrong_shape_is_refused_by_name() -> None:
    table = {
        "jev-1.13": {
            "repoqa-qa": {
                **_table()["jev-1.13"]["repoqa-qa"],
                "addresses_grader": {"min_confidence": 0.5},
            }
        }
    }

    with pytest.raises(MissingThresholdsError, match=r"repoqa-qa\.addresses_grader .*low, high"):
        score_answer(_repoqa(), judge=FakeJevJudgeClient(scripted={}), config=_config(table))


def test_score_refuses_an_unpinned_jev_model_by_its_key() -> None:
    with pytest.raises(JudgeConfigError, match="judge.jev.model"):
        score_answer(_repoqa(), judge=FakeJevJudgeClient(scripted={}), config=JudgeConfig())


@pytest.mark.parametrize(
    ("probability", "verdict"),
    [
        (0.0, JevVerdict.FALSE),
        (0.3, JevVerdict.FALSE),
        (0.31, JevVerdict.IN_BAND),
        (0.5, JevVerdict.IN_BAND),
        (0.69, JevVerdict.IN_BAND),
        (0.7, JevVerdict.TRUE),
        (1.0, JevVerdict.TRUE),
    ],
)
def test_a_noul_verdict_stands_only_outside_its_review_band(
    probability: float, verdict: JevVerdict
) -> None:
    plan = _repoqa()

    score = score_answer(plan, judge=_answering(plan, _nouls(probability, plan)), config=_config())

    assert score.verdicts["needle_identified"] is verdict


@pytest.mark.parametrize(
    ("contradicts", "agreement"),
    [(0.1, JevVerdict.TRUE), (0.9, JevVerdict.FALSE), (0.5, JevVerdict.IN_BAND)],
)
def test_contradicts_reference_is_reported_as_agreement(
    contradicts: float, agreement: JevVerdict
) -> None:
    plan = _repoqa()
    answers = {**_nouls(0.9, plan), "contradicts_reference": NoulAnswer(contradicts)}

    score = score_answer(plan, judge=_answering(plan, answers), config=_config())

    assert score.verdicts[AGREEMENT] is agreement
    assert "contradicts_reference" not in score.verdicts
    assert AGREEMENT == "agreement"


def test_the_rows_of_each_slice() -> None:
    chat, swe_qa, repoqa = _chat(), _swe_qa(), _repoqa()
    config = {
        "jev-1.13": {
            dataset: _table(dataset)["jev-1.13"][dataset]
            for dataset in ("repoqa-qa", "example-needle-chat", "swe-qa-questions")
        }
    }

    rows = {
        plan.dataset: sorted(
            score_answer(
                plan, judge=_answering(plan, _nouls(0.9, plan)), config=_config(config)
            ).verdicts
        )
        for plan in (chat, swe_qa, repoqa)
    }

    assert rows == {
        JudgedDataset.EXAMPLE_NEEDLE_CHAT: ["addresses_grader", "agreement", "site_0", "site_1"],
        JudgedDataset.SWE_QA_QUESTIONS: ["addresses_grader", "agreement", "gold_file_0"],
        JudgedDataset.REPOQA_QA: ["addresses_grader", "agreement", "needle_identified"],
    }


@pytest.mark.parametrize(
    ("score_value", "level"),
    [
        (0.0, CompletenessLevel.L0),
        (0.49, CompletenessLevel.L0),
        (0.5, CompletenessLevel.L1),
        (1.49, CompletenessLevel.L1),
        (1.5, CompletenessLevel.L2),
        (2.49, CompletenessLevel.L2),
        (2.5, CompletenessLevel.L3),
        (3.0, CompletenessLevel.L3),
    ],
)
def test_completeness_is_cut_at_the_level_midpoints(
    score_value: float, level: CompletenessLevel
) -> None:
    plan = _chat()
    answers = {**_nouls(0.9, plan), "completeness": ScoreAnswer(score_value, 0.8, {})}

    score = score_answer(
        plan, judge=_answering(plan, answers), config=_config(_table("example-needle-chat"))
    )

    assert score.completeness is level


def test_completeness_below_its_confidence_gate_is_undefined() -> None:
    plan = _chat()
    answers = {**_nouls(0.9, plan), "completeness": ScoreAnswer(3.0, 0.59, {})}

    score = score_answer(
        plan, judge=_answering(plan, answers), config=_config(_table("example-needle-chat"))
    )

    assert score.completeness is CompletenessLevel.UNDEFINED


def test_the_completeness_levels_are_the_questions() -> None:
    levels = [level for level in CompletenessLevel if level is not CompletenessLevel.UNDEFINED]

    assert len(levels) == len(COMPLETENESS.levels) == 4


@pytest.mark.parametrize("plan", [_repoqa(), _chat(), _swe_qa()], ids=lambda plan: plan.dataset)
def test_completeness_not_asked_is_undefined(plan: JevRequestPlan) -> None:
    unreferenced = {
        JudgedDataset.REPOQA_QA: _repoqa,
        JudgedDataset.EXAMPLE_NEEDLE_CHAT: lambda judged: _chat(2, judged),
        JudgedDataset.SWE_QA_QUESTIONS: _swe_qa,
    }[plan.dataset](_judged(reference=None))
    table = {"jev-1.13": {plan.dataset: _table(plan.dataset)["jev-1.13"][plan.dataset]}}

    score = score_answer(
        unreferenced,
        judge=_answering(unreferenced, _nouls(0.9, unreferenced)),
        config=_config(table),
    )

    assert score.completeness is CompletenessLevel.UNDEFINED
    assert score.verdicts[AGREEMENT] is JevVerdict.UNDEFINED


def test_an_outage_leaves_every_row_undefined_never_false() -> None:
    score = score_answer(
        _chat(),
        judge=FakeJevJudgeClient(scripted={}),
        config=_config(_table("example-needle-chat")),
    )

    assert set(score.verdicts.values()) == {JevVerdict.UNDEFINED}
    assert score.completeness is CompletenessLevel.UNDEFINED
    assert score.outage


def test_an_outage_in_one_part_leaves_only_its_questions_undefined() -> None:
    plan = _chat(site_count=15)
    first, _second = plan.requests
    judge = FakeJevJudgeClient.answering(
        [
            (
                first,
                JevResponse(
                    _SERVED,
                    {qid: NoulAnswer(0.9) for qid in first.questions if qid != "completeness"}
                    | {"completeness": ScoreAnswer(2.0, 0.9, {})},
                ),
            )
        ]
    )

    score = score_answer(plan, judge=judge, config=_config(_table("example-needle-chat")))

    assert [score.verdicts[f"site_{i}"] for i in (0, 11, 12, 14)] == [
        JevVerdict.TRUE,
        JevVerdict.TRUE,
        JevVerdict.UNDEFINED,
        JevVerdict.UNDEFINED,
    ]
    assert (score.request_parts, score.outage) == (2, True)


def test_an_answer_over_the_cap_is_scored_undefined_without_a_call() -> None:
    judge = FakeJevJudgeClient(scripted={})
    plan = _chat(judged=_judged(answer="x" * 12001))

    score = score_answer(plan, judge=judge, config=_config(_table("example-needle-chat")))

    assert judge.calls == 0
    assert score.answer_over_cap
    assert set(score.verdicts) == {"site_0", "site_1", "addresses_grader", AGREEMENT}
    assert set(score.verdicts.values()) == {JevVerdict.UNDEFINED}


def test_a_mismatched_model_stops_the_scoring() -> None:
    plan = _repoqa()

    class _Mismatched:
        def judge(self, request: JevRequest) -> JevResponse:
            raise JudgeModelMismatchError(model="typesafe/jev-1.14-20261001", pinned="jev-1.13")

    with pytest.raises(JudgeModelMismatchError):
        score_answer(plan, judge=_Mismatched(), config=_config())


def test_an_audit_question_never_enters_a_scoring_request() -> None:
    audit = repoqa_audit_request(_judged(), jev=_JEV).request
    assert audit is not None
    smuggled = JevRequestPlan(
        JudgedDataset.REPOQA_QA, (audit,), question_ids=tuple(audit.questions)
    )

    with pytest.raises(ValueError, match="committed_function"):
        score_answer(smuggled, judge=FakeJevJudgeClient(scripted={}), config=_config())


_SLICES: dict[str, Callable[[JudgedAnswer], JevRequestPlan]] = {
    "repoqa-qa": _repoqa,
    "example-needle-chat": lambda judged: _chat(2, judged),
    "swe-qa-questions": _swe_qa,
}


@pytest.mark.parametrize("slice_name", sorted(_SLICES))
@pytest.mark.parametrize("injection", load_planted_injections(), ids=lambda each: each.id)
def test_every_planted_injection_is_flagged_on_every_slice(
    slice_name: str, injection: PlantedInjection
) -> None:
    """The plumbing half of the injection check: a flag Jev raises reaches the row.

    Whether Jev raises it on these texts is measured on the owner-gated alignment pass.
    """
    plan = _SLICES[slice_name](_judged(answer=planted_answer("It is `run`.", injection)))
    answers = {**_nouls(0.1, plan), "addresses_grader": NoulAnswer(0.98)}
    table = {"jev-1.13": {slice_name: _table(slice_name)["jev-1.13"][slice_name]}}

    score = score_answer(plan, judge=_answering(plan, answers), config=_config(table))

    assert score.verdicts["addresses_grader"] is JevVerdict.TRUE


def test_needle_identified_is_the_repoqa_question() -> None:
    (request,) = _repoqa().requests

    assert request.questions["needle_identified"] is NEEDLE_IDENTIFIED


def test_threshold_blocks_validate_their_shape() -> None:
    assert NoulBand(low=0.3, high=0.7).high == 0.7
    assert ScoreGate(min_confidence=0.5).min_confidence == 0.5
    with pytest.raises(ValueError, match="low=0.8 > high=0.2"):
        NoulBand(low=0.8, high=0.2)


@pytest.mark.parametrize(
    ("site_probabilities", "recall"),
    [((0.9, 0.9), 1.0), ((0.9, 0.1), 0.5), ((0.1, 0.1), 0.0)],
)
def test_the_jev_location_recall_is_the_share_of_sites_it_says_are_named(
    site_probabilities: tuple[float, float], recall: float
) -> None:
    plan = _chat()
    answers = {
        **_nouls(0.9, plan),
        "site_0": NoulAnswer(site_probabilities[0]),
        "site_1": NoulAnswer(site_probabilities[1]),
    }

    score = score_answer(
        plan, judge=_answering(plan, answers), config=_config(_table("example-needle-chat"))
    )

    assert score.gold_location_recall == recall


def test_the_jev_location_recall_waits_on_every_site_being_decided() -> None:
    plan = _chat()
    answers = {**_nouls(0.9, plan), "site_1": NoulAnswer(0.5)}

    score = score_answer(
        plan, judge=_answering(plan, answers), config=_config(_table("example-needle-chat"))
    )

    assert score.verdicts["site_1"] is JevVerdict.IN_BAND
    assert score.gold_location_recall is None


def test_a_single_needle_has_no_location_recall() -> None:
    plan = _repoqa()

    score = score_answer(plan, judge=_answering(plan, _nouls(0.9, plan)), config=_config())

    assert score.gold_location_recall is None


def test_the_swe_qa_location_recall_counts_gold_files() -> None:
    plan = swe_qa_request_plan(
        _judged(), (JudgedSite("pkg/a.py"), JudgedSite("pkg/b.py")), jev=_JEV
    )
    answers = {**_nouls(0.9, plan), "gold_file_1": NoulAnswer(0.05)}
    table = {
        "jev-1.13": {"swe-qa-questions": _table("swe-qa-questions")["jev-1.13"]["swe-qa-questions"]}
    }

    score = score_answer(plan, judge=_answering(plan, answers), config=_config(table))

    assert score.gold_location_recall == 0.5


class _GarbledJev:
    """A Jev that answers in no documented shape."""

    def judge(self, request: JevRequest) -> JevResponse:
        raise JudgeResponseError("Jev jev-1.13: answered with non-JSON: '<html>'")


def test_an_answer_in_no_documented_shape_is_an_outage_never_a_crash() -> None:
    score = score_answer(_repoqa(), judge=_GarbledJev(), config=_config())

    assert score.outage
    assert set(score.verdicts.values()) == {JevVerdict.UNDEFINED}


def test_an_answer_from_another_jev_than_the_thresholds_pin_is_refused() -> None:
    """A client pinned elsewhere cannot borrow the jev-1.13 bands for its answers."""
    plan = _repoqa()
    judge = FakeJevJudgeClient.answering(
        (request, JevResponse("typesafe/jev-1.14-20261001", _nouls(0.9, plan)))
        for request in plan.requests
    )

    with pytest.raises(JudgeModelMismatchError, match="expected 'jev-1.13'"):
        score_answer(plan, judge=judge, config=_config())
