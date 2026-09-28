"""The shared chat client over a mock transport: every role's wire body, its pin, and its failures.

The escalation judge calls ``/chat/completions`` one request at a time; a
``:batch`` role (both labellers, the reference writer and its fallback) runs its
requests as one batch on OpenRouter's Batch API and waits on it within the
role's timeout (the live Batch API docs: the batch names the base model slug and
OpenRouter resolves the ``:batch`` entry itself).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
import pytest

from pydocs_eval.judge.chat_wire import (
    ChatCompletion,
    ChatFailure,
    ChatMessage,
    ChatRequest,
    MessageRole,
    StructuredOutput,
)
from pydocs_eval.judge.config import load_judge_deployment
from pydocs_eval.judge.openrouter_chat import (
    ChatCompleter,
    FakeOpenRouterChatClient,
    OpenRouterChatClient,
)
from pydocs_eval.judge.openrouter_http import JudgeModelMismatchError, JudgeRequestError
from pydocs_eval.judge.roles import (
    ChatRole,
    escalation_role,
    labeller_roles,
    reference_writer_fallback_role,
    reference_writer_role,
)

_GOLDENS = Path(__file__).parent / "goldens"
_DEPLOYMENT = load_judge_deployment(
    Path(__file__).resolve().parents[2] / "configs" / "judge_openrouter.yaml"
)
_BEARER = "sk-or-v1-planted-chat-bearer-fedcba9876543210"
_OUTPUT = StructuredOutput(
    name="verdict",
    schema={
        "type": "object",
        "properties": {"verdict": {"type": "boolean"}, "evidence": {"type": "string"}},
        "required": ["verdict", "evidence"],
        "additionalProperties": False,
    },
)
_CONTENT = {"verdict": True, "evidence": "It names `get_params`."}


def _request(custom_id: str = "q01") -> ChatRequest:
    messages = (
        ChatMessage(MessageRole.SYSTEM, "You label answers."),
        ChatMessage(MessageRole.USER, "Does the answer name the function?"),
    )
    return ChatRequest(custom_id=custom_id, messages=messages, output=_OUTPUT)


_ROLES: dict[str, ChatRole] = {
    "escalation": escalation_role(_DEPLOYMENT.judge),
    "labeller_0": labeller_roles(_DEPLOYMENT.judge)[0],
    "labeller_1": labeller_roles(_DEPLOYMENT.judge)[1],
    "reference_writer": reference_writer_role(_DEPLOYMENT.reference_writer),
    "reference_writer_fallback": reference_writer_fallback_role(_DEPLOYMENT.reference_writer),
}
_BATCH_ROLES = ("labeller_0", "labeller_1", "reference_writer", "reference_writer_fallback")


def _completion(
    model: str, content: object = _CONTENT, *, cost: float | None = 0.0021
) -> dict[str, object]:
    body: dict[str, object] = {
        "id": "gen-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }
    if cost is not None:
        body["usage"] = {"prompt_tokens": 120, "completion_tokens": 30, "cost": cost}
    return body


class _OpenRouter:
    """A mock OpenRouter: sync completions, and batches that finish after ``polls_to_finish`` polls."""

    def __init__(
        self,
        *,
        served: str,
        completion: Callable[[httpx.Request], httpx.Response] | None = None,
        polls_to_finish: int = 1,
        finished: Mapping[str, object] | None = None,
        submit: Callable[[httpx.Request], httpx.Response] | None = None,
        poll_failures: tuple[int, ...] = (),
    ) -> None:
        self.served = served
        self.completion = completion or (
            lambda request: httpx.Response(200, json=_completion(served))
        )
        self.polls_to_finish = polls_to_finish
        self.finished = finished
        self.submit = submit
        self.poll_failures = list(poll_failures)
        self.requests: list[httpx.Request] = []
        self.submitted: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/chat/completions"):
            return self.completion(request)
        if request.method == "POST" and path.endswith("/batches"):
            return self._submit(request)
        return self._poll(request)

    def _submit(self, request: httpx.Request) -> httpx.Response:
        if self.submit is not None:
            return self.submit(request)
        self.submitted = json.loads(request.content)
        return httpx.Response(
            202, json={"id": "batch_123", "object": "batch", "status": "validating"}
        )

    def _poll(self, request: httpx.Request) -> httpx.Response:
        if self.poll_failures:
            return httpx.Response(self.poll_failures.pop(0), text="upstream")
        polls = sum(1 for sent in self.requests if sent.method == "GET")
        if polls < self.polls_to_finish:
            return httpx.Response(
                200, json={"id": "batch_123", "status": "in_progress", "results": None}
            )
        return httpx.Response(200, json=self.finished or self._completed())

    def _completed(self) -> dict[str, object]:
        results = [
            {
                "id": f"batch_req_{i}",
                "custom_id": item["custom_id"],
                "response": {
                    "status_code": 200,
                    "request_id": f"r{i}",
                    "body": _completion(self.served, cost=None),
                },
                "error": None,
            }
            for i, item in enumerate(self.submitted.get("requests", []))  # type: ignore[union-attr]
        ]
        return {"id": "batch_123", "status": "completed", "results": results, "error": None}


class _Clock:
    """A clock the client's waits advance, so a deadline passes without real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def bearer(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("OPENROUTER_API_KEY", _BEARER)
    return _BEARER


def _client(
    role: ChatRole, openrouter: _OpenRouter, clock: _Clock | None = None
) -> OpenRouterChatClient:
    clock = clock or _Clock()
    return OpenRouterChatClient(
        role=role,
        http=httpx.Client(transport=httpx.MockTransport(openrouter)),
        sleep=clock.sleep,
        clock=lambda: clock.now,
    )


def _golden(name: str) -> dict[str, object]:
    return json.loads((_GOLDENS / name).read_text(encoding="utf-8"))


def test_the_escalation_judge_sends_its_golden(bearer: str) -> None:
    openrouter = _OpenRouter(served="openai/gpt-6-luna")

    (outcome,) = _client(_ROLES["escalation"], openrouter).complete_all([_request()])

    (sent,) = openrouter.requests
    assert (sent.method, str(sent.url)) == ("POST", "https://openrouter.ai/api/v1/chat/completions")
    assert sent.headers["authorization"] == f"Bearer {bearer}"
    assert json.loads(sent.content) == _golden("chat_escalation_request.json")
    assert set(sent.extensions["timeout"].values()) == {120.0}
    assert outcome == ChatCompletion("q01", "openai/gpt-6-luna", _CONTENT, cost_usd=0.0021)


def test_a_batch_role_submits_its_golden_and_names_the_base_slug(bearer: str) -> None:
    openrouter = _OpenRouter(served="anthropic/claude-opus-5.5")

    _client(_ROLES["labeller_1"], openrouter).complete_all([_request("q01"), _request("q02")])

    submit = openrouter.requests[0]
    assert (submit.method, str(submit.url)) == ("POST", "https://openrouter.ai/api/v1/batches")
    assert json.loads(submit.content) == _golden("chat_batch_request.json")
    assert list(json.loads(submit.content)) == ["endpoint", "model", "requests"]


@pytest.mark.parametrize("role_name", _BATCH_ROLES)
def test_every_batch_role_sends_the_batch_golden_under_its_own_model(
    bearer: str, role_name: str
) -> None:
    role = _ROLES[role_name]
    openrouter = _OpenRouter(served=role.model.split(":")[0])

    _client(role, openrouter).complete_all([_request("q01"), _request("q02")])

    expected = {**_golden("chat_batch_request.json"), "model": role.model.split(":")[0]}
    assert json.loads(openrouter.requests[0].content) == expected


def test_a_batch_is_polled_until_it_completes_and_read_in_request_order(bearer: str) -> None:
    openrouter = _OpenRouter(served="openai/gpt-6-astra", polls_to_finish=3)
    clock = _Clock()

    outcomes = _client(_ROLES["labeller_0"], openrouter, clock).complete_all(
        [_request("q02"), _request("q01")]
    )

    polls = [sent for sent in openrouter.requests if sent.method == "GET"]
    assert [str(poll.url) for poll in polls] == [
        "https://openrouter.ai/api/v1/batches/batch_123"
    ] * 3
    assert [outcome.custom_id for outcome in outcomes] == ["q02", "q01"]
    assert all(isinstance(outcome, ChatCompletion) for outcome in outcomes)
    assert clock.waits == [15.0, 15.0]


@pytest.mark.parametrize(
    ("role_name", "served"),
    [
        ("escalation", "openai/gpt-6-luna-mini"),
        ("labeller_0", "openai/gpt-6-astra-2"),
        ("labeller_1", "anthropic/claude-opus-5.6"),
        ("reference_writer", "anthropic/claude-sonnet-5"),
        ("reference_writer_fallback", "anthropic/claude-opus-5.5"),
    ],
)
def test_every_role_client_refuses_another_model_naming_both(
    bearer: str, role_name: str, served: str
) -> None:
    role = _ROLES[role_name]

    with pytest.raises(JudgeModelMismatchError) as mismatch:
        _client(role, _OpenRouter(served=served)).complete_all([_request()])

    assert str(mismatch.value) == f"judge model mismatch: got {served!r}, expected {role.model!r}"


@pytest.mark.parametrize(
    ("role_name", "served"),
    [
        ("escalation", "openai/gpt-6-luna-2026-09-01"),
        ("labeller_0", "openai/gpt-6-astra"),
        ("labeller_1", "anthropic/claude-opus-5.5-20260901"),
    ],
)
def test_the_pinned_model_is_accepted_as_openrouter_names_it(
    bearer: str, role_name: str, served: str
) -> None:
    (outcome,) = _client(_ROLES[role_name], _OpenRouter(served=served)).complete_all([_request()])

    assert isinstance(outcome, ChatCompletion)
    assert outcome.served_model == served


def test_a_sync_outage_fails_only_its_row(bearer: str) -> None:
    replies = iter([httpx.Response(503), httpx.Response(503), httpx.Response(503)])
    openrouter = _OpenRouter(
        served="openai/gpt-6-luna",
        completion=lambda request: next(
            replies, httpx.Response(200, json=_completion("openai/gpt-6-luna"))
        ),
    )

    first, second = _client(_ROLES["escalation"], openrouter).complete_all(
        [_request("q01"), _request("q02")]
    )

    assert isinstance(first, ChatFailure) and "3 attempts" in first.reason
    assert isinstance(second, ChatCompletion)
    assert len(openrouter.requests) == 4


def test_a_refused_request_raises_without_a_retry(bearer: str) -> None:
    openrouter = _OpenRouter(
        served="openai/gpt-6-luna",
        completion=lambda request: httpx.Response(400, text="bad schema"),
    )

    with pytest.raises(JudgeRequestError, match="HTTP 400"):
        _client(_ROLES["escalation"], openrouter).complete_all([_request()])

    assert len(openrouter.requests) == 1


def test_content_that_is_not_the_requested_json_fails_its_row(bearer: str) -> None:
    openrouter = _OpenRouter(
        served="openai/gpt-6-luna",
        completion=lambda request: httpx.Response(
            200, json=_completion("openai/gpt-6-luna", content="prose")
        ),
    )

    (outcome,) = _client(_ROLES["escalation"], openrouter).complete_all([_request()])

    assert isinstance(outcome, ChatFailure)


def test_a_batch_submit_is_never_retried(bearer: str) -> None:
    openrouter = _OpenRouter(
        served="openai/gpt-6-astra", submit=lambda request: httpx.Response(503)
    )

    outcomes = _client(_ROLES["labeller_0"], openrouter).complete_all(
        [_request("q01"), _request("q02")]
    )

    assert len(openrouter.requests) == 1
    assert [type(outcome) for outcome in outcomes] == [ChatFailure, ChatFailure]


def test_a_failing_poll_is_retried(bearer: str) -> None:
    openrouter = _OpenRouter(served="openai/gpt-6-astra", poll_failures=(503, 502))

    (outcome,) = _client(_ROLES["labeller_0"], openrouter).complete_all([_request()])

    assert isinstance(outcome, ChatCompletion)


@pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
def test_a_batch_that_ends_without_results_fails_every_row_naming_it(
    bearer: str, status: str
) -> None:
    finished = {
        "id": "batch_123",
        "status": status,
        "results": None,
        "error": {"message": "provider rejected it"},
    }
    openrouter = _OpenRouter(served="openai/gpt-6-astra", finished=finished)

    outcomes = _client(_ROLES["labeller_0"], openrouter).complete_all(
        [_request("q01"), _request("q02")]
    )

    for outcome in outcomes:
        assert isinstance(outcome, ChatFailure)
        assert (outcome.batch_id, status in outcome.reason) == ("batch_123", True)
        assert "provider rejected it" in outcome.reason


def test_a_batch_row_that_errored_or_is_missing_fails_alone(bearer: str) -> None:
    results = [
        {
            "custom_id": "q01",
            "response": {"status_code": 200, "body": _completion("openai/gpt-6-astra")},
            "error": None,
        },
        {"custom_id": "q02", "response": None, "error": {"message": "context too long"}},
    ]
    finished = {"id": "batch_123", "status": "completed", "results": results, "error": None}
    openrouter = _OpenRouter(served="openai/gpt-6-astra", finished=finished)

    first, second, third = _client(_ROLES["labeller_0"], openrouter).complete_all(
        [_request("q01"), _request("q02"), _request("q03")]
    )

    assert isinstance(first, ChatCompletion)
    assert isinstance(second, ChatFailure) and "context too long" in second.reason
    assert isinstance(third, ChatFailure) and third.batch_id == "batch_123"


def test_a_batch_past_the_role_deadline_fails_every_row_and_logs_its_id(
    bearer: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    openrouter = _OpenRouter(served="anthropic/claude-opus-5.5", polls_to_finish=10_000)
    clock = _Clock()

    outcomes = _client(_ROLES["reference_writer"], openrouter, clock).complete_all(
        [_request("q01")]
    )

    (outcome,) = outcomes
    assert isinstance(outcome, ChatFailure)
    assert outcome.batch_id == "batch_123"
    assert "600" in outcome.reason
    assert clock.now == pytest.approx(600.0)
    logged = [json.loads(record.getMessage()) for record in caplog.records]
    assert {"event": "judge_batch_abandoned", "batch_id": "batch_123"}.items() <= logged[-1].items()


def test_a_batch_that_cannot_be_polled_fails_every_row_and_logs_its_id(
    bearer: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    openrouter = _OpenRouter(served="openai/gpt-6-astra", poll_failures=(503, 503, 503))

    (outcome,) = _client(_ROLES["labeller_0"], openrouter).complete_all([_request()])

    assert isinstance(outcome, ChatFailure)
    assert (outcome.batch_id, "could not be polled" in outcome.reason) == ("batch_123", True)
    logged = json.loads(caplog.records[-1].getMessage())
    assert (logged["event"], logged["batch_id"]) == ("judge_batch_abandoned", "batch_123")


def test_duplicate_row_ids_are_refused_before_any_call(bearer: str) -> None:
    openrouter = _OpenRouter(served="openai/gpt-6-astra")

    with pytest.raises(ValueError, match="q01"):
        _client(_ROLES["labeller_0"], openrouter).complete_all([_request("q01"), _request("q01")])

    assert openrouter.requests == []


@pytest.mark.parametrize(
    "openrouter",
    [
        _OpenRouter(
            served="x", completion=lambda request: httpx.Response(401, text=f"bad key {_BEARER}")
        ),
        _OpenRouter(served=f"x/{_BEARER}"),
    ],
)
def test_no_sync_error_carries_the_bearer(
    bearer: str, caplog: pytest.LogCaptureFixture, openrouter: _OpenRouter
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises((JudgeRequestError, JudgeModelMismatchError)) as failure:
        _client(_ROLES["escalation"], openrouter).complete_all([_request()])

    assert bearer not in str(failure.value)
    assert bearer not in caplog.text


@pytest.mark.parametrize(
    "openrouter",
    [
        _OpenRouter(
            served="x", submit=lambda request: httpx.Response(401, text=f"Bearer {_BEARER} refused")
        ),
        _OpenRouter(served=f"x/{_BEARER}"),
    ],
)
def test_no_batch_error_carries_the_bearer(
    bearer: str, caplog: pytest.LogCaptureFixture, openrouter: _OpenRouter
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises((JudgeRequestError, JudgeModelMismatchError)) as failure:
        _client(_ROLES["labeller_0"], openrouter).complete_all([_request()])

    assert bearer not in str(failure.value)
    assert bearer not in caplog.text


def test_the_fake_answers_scripted_rows_and_keeps_every_request() -> None:
    fake = FakeOpenRouterChatClient(scripted={"q01": _CONTENT}, served_model="openai/gpt-6-astra")

    first, second = fake.complete_all([_request("q01"), _request("q02")])

    assert first == ChatCompletion("q01", "openai/gpt-6-astra", _CONTENT)
    assert isinstance(second, ChatFailure)
    assert [request.custom_id for request in fake.requests] == ["q01", "q02"]


def test_both_clients_are_chat_completers() -> None:
    client = _client(_ROLES["escalation"], _OpenRouter(served="openai/gpt-6-luna"))

    assert isinstance(client, ChatCompleter)
    assert isinstance(FakeOpenRouterChatClient(scripted={}), ChatCompleter)
