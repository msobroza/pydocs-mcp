"""A batch's whole life on OpenRouter: why a row failed, its id at submit, collecting it, deleting it.

The Batch API has a 24 h window and no cancel endpoint, so a batch still running
when its role's deadline passes is not a failed job: it is collected later by id
(``GET /batches/{id}``), never re-bought on the fallback. Every failure therefore
says what kind it is. A batch whose results are stored is deleted
(``DELETE /batches/{id}``); OpenRouter otherwise keeps its inputs and results
for 30 days (the live Batch API docs, 2026-09-30).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import httpx
import pytest

from pydocs_eval.judge.chat_wire import (
    BatchChatCompleter,
    ChatCompletion,
    ChatFailure,
    ChatFailureKind,
    ChatMessage,
    ChatRequest,
    MessageRole,
    StructuredOutput,
)
from pydocs_eval.judge.config import load_judge_deployment
from pydocs_eval.judge.judge_errors import JudgeRequestError
from pydocs_eval.judge.openrouter_batch import wire_custom_id
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
from pydocs_eval.judge.roles import escalation_role, reference_writer_role

from ._judge_fakes import DEPLOYMENT_YAML, FakeClock
from ._openrouter_fakes import (
    BATCH_ID,
    FakeOpenRouterEndpoint,
    completion_response,
    openrouter_client,
)

_DEPLOYMENT = load_judge_deployment(DEPLOYMENT_YAML)
_WRITER = reference_writer_role(_DEPLOYMENT.reference_writer)
_ESCALATION = escalation_role(_DEPLOYMENT.judge)
_SERVED = "anthropic/claude-opus-5.5"
_OUTPUT = StructuredOutput(name="reference", schema={"type": "object"})


def _request(custom_id: str) -> ChatRequest:
    return ChatRequest(custom_id, (ChatMessage(MessageRole.USER, "Write it."),), _OUTPUT)


def _finished(
    results: Sequence[Mapping[str, object]], status: str = "completed"
) -> dict[str, object]:
    return {"id": BATCH_ID, "status": status, "results": list(results), "error": None}


def _answered(custom_id: str, content: object = None) -> dict[str, object]:
    body = completion_response(_SERVED, {"answer": "x"} if content is None else content)
    return {"custom_id": custom_id, "response": {"status_code": 200, "body": body}, "error": None}


def _kinds(outcomes: tuple[object, ...]) -> list[ChatFailureKind | None]:
    return [o.kind if isinstance(o, ChatFailure) else None for o in outcomes]


def test_a_completed_batch_row_names_its_batch(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED)

    (outcome,) = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert isinstance(outcome, ChatCompletion)
    assert outcome.batch_id == BATCH_ID


def test_a_synchronous_row_names_no_batch(bearer: str) -> None:
    (outcome,) = openrouter_client(
        _ESCALATION, FakeOpenRouterEndpoint(served="openai/gpt-6-luna")
    ).complete_all([_request("q01")])

    assert isinstance(outcome, ChatCompletion)
    assert outcome.batch_id == ""


def test_a_row_the_batch_api_answered_with_an_error_failed_its_job(bearer: str) -> None:
    errored = {"custom_id": "q02", "response": None, "error": {"message": "overloaded"}}
    openrouter = FakeOpenRouterEndpoint(
        served=_SERVED, finished=_finished([_answered("q01"), errored])
    )

    outcomes = openrouter_client(_WRITER, openrouter).complete_all(
        [_request("q01"), _request("q02")]
    )

    assert _kinds(outcomes) == [None, ChatFailureKind.JOB_FAILED]


@pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
def test_every_row_of_a_batch_that_ended_without_results_failed_its_job(
    bearer: str, status: str
) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, finished=_finished([], status))

    outcomes = openrouter_client(_WRITER, openrouter).complete_all(
        [_request("q01"), _request("q02")]
    )

    assert _kinds(outcomes) == [ChatFailureKind.JOB_FAILED] * 2


def test_an_answer_that_is_not_the_requested_json_is_unusable_not_a_failed_job(
    bearer: str,
) -> None:
    body = completion_response(_SERVED)
    body["choices"] = [{"index": 0, "message": {"role": "assistant", "content": "prose"}}]
    prose = {"custom_id": "q01", "response": {"status_code": 200, "body": body}, "error": None}
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, finished=_finished([prose]))

    outcomes = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.UNUSABLE_ANSWER]


def test_a_batch_past_its_deadline_is_still_running_not_failed(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, polls_to_finish=10_000)

    outcomes = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.STILL_RUNNING]
    assert outcomes[0].batch_id == BATCH_ID


def test_a_batch_that_cannot_be_polled_may_still_be_running(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, poll_failures=(503, 503, 503))

    outcomes = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.STILL_RUNNING]


def test_a_failed_submit_was_never_submitted(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, submit=lambda request: httpx.Response(503))

    outcomes = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.NOT_SUBMITTED]


def test_a_synchronous_outage_failed_its_job(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(
        served="openai/gpt-6-luna", completion=lambda request: httpx.Response(503)
    )

    outcomes = openrouter_client(_ESCALATION, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.JOB_FAILED]


def test_the_batch_id_is_handed_over_once_submitted_before_any_poll(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, polls_to_finish=3)
    seen: list[tuple[str, int]] = []

    openrouter_client(_WRITER, openrouter).complete_all(
        [_request("q01")],
        on_submitted=lambda batch_id: seen.append((batch_id, len(openrouter.sent("GET")))),
    )

    assert seen == [(BATCH_ID, 0)]


def test_no_batch_id_is_handed_over_when_the_submit_failed(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, submit=lambda request: httpx.Response(503))
    seen: list[str] = []

    openrouter_client(_WRITER, openrouter).complete_all([_request("q01")], on_submitted=seen.append)

    assert seen == []


def test_a_running_batch_is_collected_by_id_without_a_new_submit(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(
        served=_SERVED,
        polls_to_finish=2,
        finished=_finished([_answered("q02"), _answered("q01")]),
    )
    clock = FakeClock()

    outcomes = openrouter_client(_WRITER, openrouter, clock).collect(BATCH_ID, ["q01", "q02"])

    assert openrouter.sent("POST") == []
    assert {str(poll.url) for poll in openrouter.sent("GET")} == {
        f"https://openrouter.ai/api/v1/batches/{BATCH_ID}"
    }
    assert [(o.custom_id, type(o)) for o in outcomes] == [
        ("q01", ChatCompletion),
        ("q02", ChatCompletion),
    ]


def test_a_batch_still_running_at_collection_stays_running(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, polls_to_finish=10_000)

    outcomes = openrouter_client(_WRITER, openrouter).collect(BATCH_ID, ["q01"])

    assert _kinds(outcomes) == [ChatFailureKind.STILL_RUNNING]


def test_a_stored_batch_is_deleted_by_id(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED)

    openrouter_client(_WRITER, openrouter).delete_batch(BATCH_ID)

    (sent,) = openrouter.requests
    assert (sent.method, str(sent.url)) == (
        "DELETE",
        f"https://openrouter.ai/api/v1/batches/{BATCH_ID}",
    )


def test_a_batch_already_gone_counts_as_deleted(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, delete_status=404)

    openrouter_client(_WRITER, openrouter).delete_batch(BATCH_ID)

    assert len(openrouter.requests) == 1


def test_a_batch_still_running_refuses_deletion(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, delete_status=409)

    with pytest.raises(JudgeRequestError, match="409"):
        openrouter_client(_WRITER, openrouter).delete_batch(BATCH_ID)


@pytest.mark.parametrize("call", ["collect", "delete_batch"])
def test_a_synchronous_role_has_no_batch_to_collect_or_delete(bearer: str, call: str) -> None:
    client = openrouter_client(_ESCALATION, FakeOpenRouterEndpoint(served="openai/gpt-6-luna"))

    with pytest.raises(ValueError, match="openai/gpt-6-luna"):
        if call == "collect":
            client.collect(BATCH_ID, ["q01"])
        else:
            client.delete_batch(BATCH_ID)


def test_both_clients_are_batch_completers() -> None:
    client = openrouter_client(_WRITER, FakeOpenRouterEndpoint(served=_SERVED))

    assert isinstance(client, BatchChatCompleter)
    assert isinstance(FakeOpenRouterChatClient(scripted={}), BatchChatCompleter)


def test_the_fake_serves_a_scripted_list_in_order_then_fails() -> None:
    fake = FakeOpenRouterChatClient(
        scripted={"q01": [ChatFailureKind.JOB_FAILED, {"answer": "x"}]}, served_model=_SERVED
    )

    first = fake.complete_all([_request("q01")])
    second = fake.complete_all([_request("q01")])
    third = fake.complete_all([_request("q01")])

    assert _kinds(first) == [ChatFailureKind.JOB_FAILED]
    assert second == (ChatCompletion("q01", _SERVED, {"answer": "x"}, batch_id="fake_batch_2"),)
    assert _kinds(third) == [ChatFailureKind.JOB_FAILED], "a spent script fails its row"


def test_the_fake_answers_a_running_row_when_it_is_collected() -> None:
    fake = FakeOpenRouterChatClient(
        scripted={"q01": [ChatFailureKind.STILL_RUNNING, {"answer": "x"}]}, served_model=_SERVED
    )
    seen: list[str] = []

    (running,) = fake.complete_all([_request("q01")], on_submitted=seen.append)
    (collected,) = fake.collect("fake_batch_1", ["q01"])
    fake.delete_batch("fake_batch_1")

    assert (seen, fake.batches) == (["fake_batch_1"], [("fake_batch_1", ("q01",))])
    assert isinstance(running, ChatFailure) and running.batch_id == "fake_batch_1"
    assert collected == ChatCompletion("q01", _SERVED, {"answer": "x"}, batch_id="fake_batch_1")
    assert fake.deleted == ["fake_batch_1"]


# A repoqa-qa task id: longer than 64 characters, with '/', '@', '.' and ':'.
_LONG_ID = "repoqa-qa/repo_qa/Ciphey/Ciphey@5dfbe93/ciphey/iface/_modules.py::__ge__"


def test_an_id_the_batch_backend_refuses_is_sent_short_and_read_back_whole(bearer: str) -> None:
    """The first paid pilot (2026-09-30) was refused: "this provider caps custom_id at 64"."""
    openrouter = FakeOpenRouterEndpoint(served=_SERVED)

    (outcome,) = openrouter_client(_WRITER, openrouter).complete_all([_request(_LONG_ID)])

    (sent,) = openrouter.submitted_ids()
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sent), sent
    assert sent == wire_custom_id(_LONG_ID)
    assert (outcome.custom_id, type(outcome)) == (_LONG_ID, ChatCompletion)


def test_a_collected_row_is_read_under_its_wire_id(bearer: str) -> None:
    finished = _finished([_answered(wire_custom_id(_LONG_ID))])
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, finished=finished)

    (outcome,) = openrouter_client(_WRITER, openrouter).collect(BATCH_ID, [_LONG_ID])

    assert (outcome.custom_id, type(outcome)) == (_LONG_ID, ChatCompletion)


def test_a_short_plain_id_goes_on_the_wire_unchanged() -> None:
    assert wire_custom_id("q01") == "q01"


def test_wire_ids_are_stable_and_distinct() -> None:
    other = _LONG_ID.replace("__ge__", "__le__")

    assert wire_custom_id(_LONG_ID) == wire_custom_id(_LONG_ID)
    assert wire_custom_id(_LONG_ID) != wire_custom_id(other)


def test_a_batch_not_visible_yet_right_after_its_submit_is_polled_again(bearer: str) -> None:
    """The second paid pilot (2026-09-30): the first poll, right after a 202, answered 404."""
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, poll_failures=(404, 404), polls_to_finish=3)

    (outcome,) = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert isinstance(outcome, ChatCompletion)
    assert len(openrouter.sent("POST")) == 1


def test_a_fresh_batch_never_visible_by_the_deadline_is_still_running_not_lost(
    bearer: str,
) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, poll_failures=(404,) * 10_000)

    outcomes = openrouter_client(_WRITER, openrouter).complete_all([_request("q01")])

    assert _kinds(outcomes) == [ChatFailureKind.STILL_RUNNING]


def test_an_earlier_batch_answering_404_on_collect_is_refused(bearer: str) -> None:
    openrouter = FakeOpenRouterEndpoint(served=_SERVED, poll_failures=(404,))

    with pytest.raises(JudgeRequestError, match="404"):
        openrouter_client(_WRITER, openrouter).collect(BATCH_ID, ["q01"])
