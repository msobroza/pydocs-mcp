"""The Jev client over a mock transport: the wire body, the pin, bounded retries, the cache, the bearer."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_cache import JevResponseCache, jev_cache_dir
from pydocs_eval.judge.jev_client import FakeJevJudgeClient, JevJudgeClient
from pydocs_eval.judge.jev_wire import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevJudge,
    JevRequest,
    JevResponse,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    jev_cache_key,
)
from pydocs_eval.judge.judge_errors import (
    JudgeConfigError,
    JudgeModelMismatchError,
    JudgeRequestError,
    JudgeUnavailableError,
)

from ._judge_fakes import PLANTED_BEARER, golden

_PINNED = JevConfig(model="jev-1.13")

REQUEST = JevRequest(
    state={
        "task": {"question": "Which function parses the header?"},
        "agent_answer": "It is `parse_header` in `src/pkg/http.py`.",
    },
    questions={
        "is_named": NoulQuestion(
            instructions="`agent_answer` names a function.",
            when_true="A function is named.",
            when_false="No function is named.",
        ),
        "depth": ScoreQuestion(
            instructions="How much of the code does `agent_answer` explain?",
            levels=("None of it.", "Some of it.", "All of it."),
        ),
        "which": ChoiceQuestion(
            instructions="Which function does `agent_answer` name?",
            options={"parse_header": None, "none_of_these": "No function is named."},
        ),
    },
)


class FakeSystemOneEndpoint:
    """A mock systemone endpoint: answers each call from ``replies`` in turn and keeps the requests."""

    def __init__(self, *replies: Callable[[httpx.Request], httpx.Response]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies[min(len(self.requests), len(self.replies)) - 1](request)


def _answering(
    payload: dict[str, object] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    body = payload if payload is not None else golden("jev_systemone_response.json")
    return lambda request: httpx.Response(200, json=body)


def _status(code: int, text: str = "") -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, text=text or f"status {code}")


def _timing_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("read timed out", request=request)


def _client(
    transport: FakeSystemOneEndpoint, cache_dir: Path, config: JevConfig = _PINNED
) -> JevJudgeClient:
    return JevJudgeClient(
        config=config,
        cache=JevResponseCache(cache_dir),
        http=httpx.Client(transport=httpx.MockTransport(transport)),
        sleep=lambda seconds: None,
    )


def test_the_request_is_the_systemonegolden(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_answering())

    _client(transport, tmp_path).judge(REQUEST)

    (sent,) = transport.requests
    assert (sent.method, str(sent.url)) == ("POST", "https://openrouter.ai/api/v1/systemone")
    assert sent.headers["authorization"] == f"Bearer {bearer}"
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == golden("jev_systemone_request.json")


def test_the_configured_timeout_bounds_the_call(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_answering())

    _client(transport, tmp_path).judge(REQUEST)

    assert set(transport.requests[0].extensions["timeout"].values()) == {10.0}


def test_the_answers_are_read_by_question_id(tmp_path: Path, bearer: str) -> None:
    response = _client(FakeSystemOneEndpoint(_answering()), tmp_path).judge(REQUEST)

    assert response.served_model == "typesafe/jev-1.13-20260917"
    assert response.answers["is_named"] == NoulAnswer(probability=0.97)
    assert response.answers["depth"] == ScoreAnswer(
        score=1.9, confidence=0.9, probabilities={"0": 0.0, "1": 0.1, "2": 0.9}
    )
    assert response.answers["which"] == ChoiceAnswer(
        choice="parse_header",
        confidence=0.95,
        probabilities={"parse_header": 0.97, "none_of_these": 0.03},
    )
    assert response.cost_usd == pytest.approx(0.000019992)


def test_a_response_from_another_model_raises_naming_both(tmp_path: Path, bearer: str) -> None:
    other = {**golden("jev_systemone_response.json"), "model": "typesafe/jev-1.14-20261001"}

    with pytest.raises(JudgeModelMismatchError) as mismatch:
        _client(FakeSystemOneEndpoint(_answering(other)), tmp_path).judge(REQUEST)

    assert str(mismatch.value) == (
        "judge model mismatch: got 'typesafe/jev-1.14-20261001', expected 'jev-1.13'"
    )


def test_a_mismatched_answer_is_never_cached(tmp_path: Path, bearer: str) -> None:
    other = {**golden("jev_systemone_response.json"), "model": "typesafe/jev-1.14-20261001"}
    with pytest.raises(JudgeModelMismatchError):
        _client(FakeSystemOneEndpoint(_answering(other)), tmp_path).judge(REQUEST)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("replies", "calls"),
    [
        ((_status(503), _status(502), _status(500)), 3),
        ((_timing_out, _timing_out, _timing_out), 3),
        ((_status(529), _timing_out, _status(503)), 3),
    ],
)
def test_a_timeout_or_5xx_is_retried_twice_then_reads_as_an_outage(
    tmp_path: Path, bearer: str, replies: tuple[Callable[..., httpx.Response], ...], calls: int
) -> None:
    transport = FakeSystemOneEndpoint(*replies)

    with pytest.raises(JudgeUnavailableError):
        _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == calls


def test_a_retried_call_that_then_answers_is_the_answer(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_status(503), _answering())

    response = _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == 2
    assert response.answers["is_named"] == NoulAnswer(probability=0.97)


def test_retries_back_off_between_attempts(tmp_path: Path, bearer: str) -> None:
    waits: list[float] = []
    client = JevJudgeClient(
        config=_PINNED,
        cache=JevResponseCache(tmp_path),
        http=httpx.Client(transport=httpx.MockTransport(FakeSystemOneEndpoint(_status(503)))),
        sleep=waits.append,
    )

    with pytest.raises(JudgeUnavailableError):
        client.judge(REQUEST)

    assert waits == [0.5, 1.0]


@pytest.mark.parametrize("code", [400, 401, 402, 404, 422])
def test_a_4xx_is_never_retried(tmp_path: Path, bearer: str, code: int) -> None:
    transport = FakeSystemOneEndpoint(_status(code))

    with pytest.raises(JudgeRequestError, match=f"HTTP {code}"):
        _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == 1


def test_a_rate_limit_is_an_outage_never_retried(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_status(429))

    with pytest.raises(JudgeUnavailableError, match="429"):
        _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == 1


def test_no_retries_configured_means_one_attempt(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_status(503))

    with pytest.raises(JudgeUnavailableError):
        _client(transport, tmp_path, JevConfig(model="jev-1.13", retries=0)).judge(REQUEST)

    assert len(transport.requests) == 1


def test_a_warm_cache_makes_no_call(tmp_path: Path, bearer: str) -> None:
    transport = FakeSystemOneEndpoint(_answering())
    client = _client(transport, tmp_path)

    first = client.judge(REQUEST)
    second = client.judge(REQUEST)

    assert len(transport.requests) == 1
    assert second == first


def test_a_cache_written_by_one_client_serves_another(tmp_path: Path, bearer: str) -> None:
    _client(FakeSystemOneEndpoint(_answering()), tmp_path).judge(REQUEST)
    transport = FakeSystemOneEndpoint(_status(500))

    _client(transport, tmp_path).judge(REQUEST)

    assert transport.requests == []


def test_the_cache_key_of_a_fixed_request_is_pinned() -> None:
    """sha256 of the golden body as sorted, compact JSON: a canonicalization change moves it."""
    assert jev_cache_key(REQUEST.body("jev-1.13")) == (
        "2e4dca08459a1504d3bbc12460d99fe5137f4b09b5f7dd7987de7a602dd1c795"
    )


def test_the_model_pin_is_part_of_the_cache_key(tmp_path: Path, bearer: str) -> None:
    assert jev_cache_key(REQUEST.body("jev-1.13")) != jev_cache_key(REQUEST.body("jev-1.14"))
    _client(FakeSystemOneEndpoint(_answering()), tmp_path).judge(REQUEST)
    transport = FakeSystemOneEndpoint(
        _answering({**golden("jev_systemone_response.json"), "model": "jev-1.14"})
    )

    _client(transport, tmp_path, JevConfig(model="jev-1.14")).judge(REQUEST)

    assert len(transport.requests) == 1


def test_an_unreadable_cache_entry_is_a_miss(tmp_path: Path, bearer: str) -> None:
    key = jev_cache_key(REQUEST.body("jev-1.13"))
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
    transport = FakeSystemOneEndpoint(_answering())

    _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == 1
    assert json.loads((tmp_path / f"{key}.json").read_text(encoding="utf-8"))["model"] == (
        "typesafe/jev-1.13-20260917"
    )


def test_the_cache_directory_defaults_under_the_bench_cache() -> None:
    assert jev_cache_dir(JevConfig()) == Path("~/.pydocs-mcp/bench/jev").expanduser()
    assert jev_cache_dir(JevConfig(cache_dir="~/judge-cache")) == (
        Path("~/judge-cache").expanduser()
    )


def test_the_bearer_is_read_when_the_call_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    transport = FakeSystemOneEndpoint(_answering())
    client = _client(transport, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-set-after-construction")

    client.judge(REQUEST)

    assert transport.requests[0].headers["authorization"] == (
        "Bearer sk-or-v1-set-after-construction"
    )


def test_a_missing_key_refuses_by_its_variable_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    transport = FakeSystemOneEndpoint(_answering())

    with pytest.raises(JudgeConfigError, match=r"\$OPENROUTER_API_KEY"):
        _client(transport, tmp_path).judge(REQUEST)

    assert transport.requests == []


def test_an_unpinned_jev_model_refuses_by_its_key(tmp_path: Path) -> None:
    with pytest.raises(JudgeConfigError, match="judge.jev.model"):
        _client(FakeSystemOneEndpoint(_answering()), tmp_path, JevConfig())


@pytest.mark.parametrize(
    "reply",
    [
        _status(401, f'{{"error": "bad key {PLANTED_BEARER}"}}'),
        _status(400, f"Authorization: Bearer {PLANTED_BEARER} rejected"),
        _status(503, f"upstream said Bearer {PLANTED_BEARER}"),
        _timing_out,
        _answering({**golden("jev_systemone_response.json"), "model": f"x/{PLANTED_BEARER}"}),
        lambda request: httpx.Response(200, text=f"<html>{PLANTED_BEARER}</html>"),
        _answering(
            {
                **golden("jev_systemone_response.json"),
                "answers": {"is_named": {"type": "noul", "noul": PLANTED_BEARER}},
            }
        ),
        _answering({**golden("jev_systemone_response.json"), "answers": PLANTED_BEARER}),
    ],
)
def test_no_error_or_log_line_carries_the_bearer(
    tmp_path: Path,
    bearer: str,
    caplog: pytest.LogCaptureFixture,
    reply: Callable[[httpx.Request], httpx.Response],
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(Exception) as failure:
        _client(FakeSystemOneEndpoint(reply), tmp_path).judge(REQUEST)

    assert bearer not in str(failure.value)
    assert bearer not in repr(failure.value)
    assert bearer not in caplog.text


def test_the_fake_answers_what_it_was_scripted_with_and_counts_calls() -> None:
    fake = FakeJevJudgeClient.answering([(REQUEST, _answer_of_every_question())])

    response = fake.judge(REQUEST)

    assert response.answers["is_named"] == NoulAnswer(probability=0.9)
    assert fake.calls == 1


def test_an_unscripted_request_to_the_fake_is_an_outage() -> None:
    fake = FakeJevJudgeClient(scripted={})

    with pytest.raises(JudgeUnavailableError):
        fake.judge(REQUEST)

    assert fake.calls == 1


def test_both_clients_are_jev_judges(tmp_path: Path) -> None:
    assert isinstance(_client(FakeSystemOneEndpoint(_answering()), tmp_path), JevJudge)
    assert isinstance(FakeJevJudgeClient(scripted={}), JevJudge)


def _answer_of_every_question() -> JevResponse:
    return JevResponse(
        served_model="typesafe/jev-1.13-20260917",
        answers={
            "is_named": NoulAnswer(probability=0.9),
            "depth": ScoreAnswer(score=2.0, confidence=1.0, probabilities={"2": 1.0}),
            "which": ChoiceAnswer(choice="parse_header", confidence=1.0, probabilities={}),
        },
    )


def test_a_cache_entry_that_no_longer_parses_is_refetched(tmp_path: Path, bearer: str) -> None:
    key = jev_cache_key(REQUEST.body("jev-1.13"))
    (tmp_path / f"{key}.json").write_text(
        '{"model": "typesafe/jev-1.13-20260917"}', encoding="utf-8"
    )
    transport = FakeSystemOneEndpoint(_answering())

    response = _client(transport, tmp_path).judge(REQUEST)

    assert len(transport.requests) == 1
    assert response.answers["is_named"] == NoulAnswer(probability=0.97)


def test_a_cost_that_is_not_a_number_reads_as_unknown(tmp_path: Path, bearer: str) -> None:
    body = {**golden("jev_systemone_response.json"), "usage": {"cost": True, "input_tokens": 1}}

    response = _client(FakeSystemOneEndpoint(_answering(body)), tmp_path).judge(REQUEST)

    assert response.cost_usd is None
