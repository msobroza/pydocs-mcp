"""The escalation judge: in-band answers only, trusted questions only, cached, and capped."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.judge.alignment_adjudication import adjudicate
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot
from pydocs_eval.judge.chat_wire import ChatFailureKind
from pydocs_eval.judge.config import JevConfig, JudgeConfig
from pydocs_eval.judge.escalation import (
    EscalationCache,
    EscalationJudge,
    EscalationSpendLedger,
    build_escalation_judge,
    escalate_in_band,
)
from pydocs_eval.judge.jev_scoring import AGREEMENT, JevScore
from pydocs_eval.judge.judge_errors import JudgeConfigError
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.judge.thresholds import CompletenessLevel, JevVerdict

from ._alignment_fakes import answered, labelled, labels_of, repoqa_context, repoqa_row

_LUNA = "openai/gpt-6-luna"
_JEV = JevConfig(model="jev-1.13")
_EVERY_REPOQA_QUESTION = frozenset(
    {
        ("repoqa-qa", "needle_identified"),
        ("repoqa-qa", "addresses_grader"),
        ("repoqa-qa", "contradicts_reference"),
    }
)


def _content(**verdicts: str) -> dict[str, object]:
    return {
        question_id: {"verdict": verdict, "evidence": "line", "confidence": "high"}
        for question_id, verdict in verdicts.items()
    }


def _judge(
    tmp_path: Path,
    client: FakeOpenRouterChatClient,
    *,
    trusted: frozenset[tuple[str, str]] = _EVERY_REPOQA_QUESTION,
    spend_cap_usd: float = 2.0,
) -> EscalationJudge:
    return EscalationJudge(
        client=client,
        model=_LUNA,
        jev=_JEV,
        trusted=trusted,
        cache=EscalationCache(tmp_path / "escalation"),
        ledger=EscalationSpendLedger(tmp_path / "escalation" / "spend_ledger.jsonl"),
        spend_cap_usd=spend_cap_usd,
    )


def test_a_trusted_in_band_question_is_decided_by_the_escalation_judge(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient({"answer_1": _content(addresses_grader="false")}, _LUNA)

    verdicts = _judge(tmp_path, client).decide("answer_1", repoqa_context(), ["addresses_grader"])

    assert verdicts == {"addresses_grader": JevVerdict.FALSE}
    (request,) = client.requests
    assert list(request.output.schema["properties"]) == ["addresses_grader"]


def test_a_question_below_the_floor_reads_undecided_for_every_in_band_answer_without_a_call(
    tmp_path: Path,
) -> None:
    client = FakeOpenRouterChatClient({}, _LUNA)
    judge = _judge(tmp_path, client, trusted=frozenset({("repoqa-qa", "needle_identified")}))

    verdicts = judge.decide("answer_1", repoqa_context(), ["addresses_grader"])
    again = judge.decide("answer_2", repoqa_context("Another answer."), ["addresses_grader"])

    assert verdicts == again == {"addresses_grader": JevVerdict.UNDECIDED}
    assert client.requests == []


def test_a_second_scoring_pass_makes_zero_calls(tmp_path: Path) -> None:
    content = {"answer_1": _content(needle_identified="true", addresses_grader="false")}
    first_pass = FakeOpenRouterChatClient(content, _LUNA)
    second_pass = FakeOpenRouterChatClient({}, _LUNA)
    asked = ["needle_identified", "addresses_grader"]

    first = _judge(tmp_path, first_pass).decide("answer_1", repoqa_context(), asked)
    second = _judge(tmp_path, second_pass).decide("answer_1", repoqa_context(), asked)

    assert first == second == {"needle_identified": JevVerdict.TRUE, "addresses_grader": JevVerdict.FALSE}
    assert (len(first_pass.requests), len(second_pass.requests)) == (1, 0)


def test_the_spend_cap_stops_calls_and_books_undecided(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient({"answer_1": _content(addresses_grader="false")}, _LUNA)
    judge = _judge(tmp_path, client)
    judge.ledger.record("earlier", 1.97)

    verdicts = judge.decide("answer_1", repoqa_context(), ["addresses_grader"])

    assert verdicts == {"addresses_grader": JevVerdict.UNDECIDED}
    assert client.requests == []


def test_a_zero_cap_never_calls(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient({"answer_1": _content(addresses_grader="false")}, _LUNA)

    verdicts = _judge(tmp_path, client, spend_cap_usd=0.0).decide(
        "answer_1", repoqa_context(), ["addresses_grader"]
    )

    assert verdicts == {"addresses_grader": JevVerdict.UNDECIDED}
    assert client.requests == []


@pytest.mark.parametrize(("reported", "booked"), [(0.004, 0.004), (None, 0.05)])
def test_every_call_is_metered_and_an_unpriced_one_at_its_bound(
    tmp_path: Path, reported: float | None, booked: float
) -> None:
    client = FakeOpenRouterChatClient(
        {"answer_1": _content(addresses_grader="false")}, _LUNA, batch_cost_usd=reported
    )
    judge = _judge(tmp_path, client)

    judge.decide("answer_1", repoqa_context(), ["addresses_grader"])

    assert judge.ledger.spent_usd() == pytest.approx(booked)
    (line,) = judge.ledger.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["priced"] is (reported is not None)


def test_an_outage_reads_undefined_never_false_and_is_asked_again(tmp_path: Path) -> None:
    failing = FakeOpenRouterChatClient({"answer_1": ChatFailureKind.JOB_FAILED}, _LUNA)
    later = FakeOpenRouterChatClient({"answer_1": _content(addresses_grader="true")}, _LUNA)

    first = _judge(tmp_path, failing).decide("answer_1", repoqa_context(), ["addresses_grader"])
    second = _judge(tmp_path, later).decide("answer_1", repoqa_context(), ["addresses_grader"])

    assert first == {"addresses_grader": JevVerdict.UNDEFINED}
    assert second == {"addresses_grader": JevVerdict.TRUE}


def test_an_answer_outside_the_schema_reads_undefined_and_is_not_cached(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient({"answer_1": {"addresses_grader": "yes"}}, _LUNA)
    judge = _judge(tmp_path, client)

    verdicts = judge.decide("answer_1", repoqa_context(), ["addresses_grader"])
    judge.decide("answer_1", repoqa_context(), ["addresses_grader"])

    assert verdicts == {"addresses_grader": JevVerdict.UNDEFINED}
    assert len(client.requests) == 2


def test_a_score_question_is_not_the_escalation_judges_to_decide(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="completeness"):
        _judge(tmp_path, FakeOpenRouterChatClient({}, _LUNA)).decide(
            "answer_1", repoqa_context(), ["completeness"]
        )


def _score(**verdicts: JevVerdict) -> JevScore:
    return JevScore(
        verdicts=verdicts,
        completeness=CompletenessLevel.UNDEFINED,
        answer_over_cap=False,
        outage=False,
        request_parts=1,
    )


def test_only_the_in_band_rows_of_a_jev_score_are_escalated(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient(
        {"answer_1": _content(addresses_grader="false", contradicts_reference="false")}, _LUNA
    )
    score = _score(
        needle_identified=JevVerdict.TRUE,
        addresses_grader=JevVerdict.IN_BAND,
        **{AGREEMENT: JevVerdict.IN_BAND},
    )

    escalated = escalate_in_band(
        score, item_id="answer_1", context=repoqa_context(), judge=_judge(tmp_path, client)
    )

    assert escalated.verdicts == {
        "needle_identified": JevVerdict.TRUE,
        "addresses_grader": JevVerdict.FALSE,
        AGREEMENT: JevVerdict.TRUE,
    }
    (request,) = client.requests
    assert list(request.output.schema["properties"]) == ["addresses_grader", "contradicts_reference"]


def test_a_score_with_nothing_in_band_asks_nothing(tmp_path: Path) -> None:
    client = FakeOpenRouterChatClient({}, _LUNA)
    score = _score(needle_identified=JevVerdict.FALSE, **{AGREEMENT: JevVerdict.UNDEFINED})

    escalated = escalate_in_band(
        score, item_id="answer_1", context=repoqa_context(), judge=_judge(tmp_path, client)
    )

    assert escalated == score
    assert client.requests == []


def _alignment_rows(luna_agrees: bool):
    """Ten agreed repoqa items Luna labelled: all as the labellers did, or every needle wrong."""
    good = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}
    luna = good if luna_agrees else {**good, "needle_identified": "false"}
    rows = [
        replace(
            labelled(repoqa_row(f"item_{i}"), labels_of(**good), labels_of(**good)),
            escalation_judge=answered(_LUNA, labels_of(**luna)),
        )
        for i in range(10)
    ]
    no_audit = AlignmentConfig(agreed_audit_share=0.0)
    return adjudicate(rows, config=no_audit, self_preferring=LabellerSlot.OPUS)


def test_the_trusted_questions_are_read_from_the_alignment_labels(tmp_path: Path) -> None:
    config = JudgeConfig.model_validate(
        {"jev": {"model": "jev-1.13"}, "escalation": {"model": _LUNA, "cache_dir": str(tmp_path)}}
    )

    agreeing = build_escalation_judge(
        config, _alignment_rows(luna_agrees=True), FakeOpenRouterChatClient({}, _LUNA)
    )
    disagreeing = build_escalation_judge(
        config, _alignment_rows(luna_agrees=False), FakeOpenRouterChatClient({}, _LUNA)
    )

    assert ("repoqa-qa", "needle_identified") in agreeing.trusted
    assert ("repoqa-qa", "needle_identified") not in disagreeing.trusted
    assert ("repoqa-qa", "addresses_grader") in disagreeing.trusted
    assert agreeing.cache.root == tmp_path
    assert agreeing.spend_cap_usd == 2.0


def test_an_unpinned_escalation_judge_refuses_by_its_key() -> None:
    with pytest.raises(JudgeConfigError, match=r"judge\.escalation\.model"):
        build_escalation_judge(JudgeConfig(), [], FakeOpenRouterChatClient({}, _LUNA))
