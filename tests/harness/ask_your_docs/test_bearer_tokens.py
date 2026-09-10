"""Bearer sources, the renewing httpx.Auth and the redaction boundary
(LLM-connection design §4.4 — AC-4, AC-9, AC-10, AC-11, AC-32, AC-33, AC-36,
AC-37, AC-41). Core deps only: httpx ships with the required openai dep."""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import pytest

from pydocs_mcp.harness.ask_your_docs import bearer_tokens as bt
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerRejectedError,
    BearerUnavailableError,
    EnvironmentKeyBearer,
    NoBearer,
    RenewOnStatusAuth,
    StripAuthorizationAuth,
    TokenServiceBearer,
    TokenServiceError,
    display_host,
    display_url,
    last_four_of,
    redact_bearer,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

from ._connection_fakes import FakeClock, FakeTokenService, RecordingTransport

_URL = "http://localhost:8899/access-token"


def _bearer(service: FakeTokenService, **kw) -> TokenServiceBearer:
    return TokenServiceBearer(_URL, transport=service.transport, sleep=lambda _s: None, **kw)


def test_token_service_fetches_once_and_caches() -> None:
    """AC-4: two current() calls, one HTTP GET; the stripped text body is the token."""
    service = FakeTokenService(["  tok-one-abcd\n"])
    bearer = _bearer(service)
    assert bearer.current() == "tok-one-abcd"
    assert bearer.current() == "tok-one-abcd"
    assert service.calls == 1
    status = bearer.describe()
    assert status.auth_mode is AuthMode.TOKEN_SERVICE
    assert status.last_four == "abcd" and status.renewed_at is not None
    assert bearer.peek() == "tok-one-abcd"


def test_token_field_reads_the_json_body() -> None:
    """AC-4: token_field names the JSON field that holds the token."""
    service = FakeTokenService(["tok-json-wxyz"], body_shape="json", json_key="access_token")
    bearer = _bearer(service, token_field="access_token")
    assert bearer.current() == "tok-json-wxyz"


def test_token_service_down_after_three_attempts() -> None:
    """AC-10: 3 attempts with the 2s/4s backoff, then TokenServiceError naming the URL and 3."""
    service = FakeTokenService(["never"], fail_first=3)
    sleeps: list[float] = []
    bearer = TokenServiceBearer(_URL, transport=service.transport, sleep=sleeps.append)
    with pytest.raises(TokenServiceError) as excinfo:
        bearer.current()
    assert "token service http://localhost:8899/access-token unreachable after 3 attempts" in str(
        excinfo.value
    )
    assert "status 503" in str(excinfo.value)
    assert service.calls == 3
    assert sleeps == [2.0, 4.0]
    assert bearer.last_error is not None and "unreachable" in bearer.last_error


def test_empty_token_body_is_an_error() -> None:
    """AC-41 / E3: an empty or whitespace-only body (text or JSON field) never becomes a bearer."""
    for tokens, field in (([""], None), (["   \n"], None), ([""], "access_token")):
        shape = "json" if field else "text"
        service = FakeTokenService(tokens, body_shape=shape)
        bearer = _bearer(service, token_field=field)
        with pytest.raises(TokenServiceError, match="expected a non-empty token body, got empty"):
            bearer.current()
        assert service.calls == 1  # a body-shape failure is not retried


def test_unparsable_body_names_the_shape_never_the_bytes() -> None:
    """AC-37 / E2: a JSON body under another key, or a non-JSON body, is described by shape only."""
    other_key = FakeTokenService(["tok-secret-9999"], body_shape="json", json_key="token")
    with pytest.raises(TokenServiceError) as excinfo:
        _bearer(other_key, token_field="access_token").current()
    assert "expected JSON body with field 'access_token', got keys=['token']" in str(excinfo.value)
    assert "tok-secret-9999" not in str(excinfo.value)
    html = FakeTokenService(["tok-secret-9999"], body_shape="html")
    with pytest.raises(TokenServiceError, match=r"got non-JSON body \(text/html, \d+ bytes\)"):
        _bearer(html, token_field="access_token").current()


def test_renew_is_compare_and_swap() -> None:
    """AC-32: a renew for a token that is no longer the cache returns the cache without fetching."""
    service = FakeTokenService(["t1", "t2", "t3"])
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    assert bearer.renew("t1", reason="rejected_status") == "t2"
    assert service.calls == 2
    assert bearer.renew("t1", reason="rejected_status") == "t2"  # another flow already renewed
    assert service.calls == 2


def test_renew_is_rate_limited_between_renewals() -> None:
    """AC-33: renewals inside _MIN_RENEW_INTERVAL_SECONDS return the cache; the first renew
    after the initial fetch is never rate-limited."""
    clock = FakeClock()
    service = FakeTokenService(["t1", "t2", "t3"])
    bearer = _bearer(service, now=clock)
    assert bearer.current() == "t1"
    assert bearer.renew("t1", reason="rejected_status") == "t2"  # first renew: fetches
    assert bearer.renew("t2", reason="rejected_status") == "t2"  # within the interval: cache
    assert service.calls == 2
    clock.advance(bt._MIN_RENEW_INTERVAL_SECONDS + 1)
    assert bearer.renew("t2", reason="rejected_status") == "t3"
    assert service.calls == 3


def test_concurrent_first_requests_cost_one_fetch() -> None:
    """AC-32: the double-checked lock makes N concurrent first requests one HTTP GET."""
    service = FakeTokenService(["t1"])
    bearer = _bearer(service)
    seen: list[str] = []
    threads = [threading.Thread(target=lambda: seen.append(bearer.current())) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == ["t1"] * 6
    assert service.calls == 1


def test_environment_key_bearer_reads_every_call(monkeypatch) -> None:
    """AC-11: rotating the variable rotates the value; strict raises, lenient yields ''."""
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    strict = EnvironmentKeyBearer("LLM_KEY", required=True)
    assert strict.current() == "key-one-1111"
    monkeypatch.setenv("LLM_KEY", "key-two-2222")
    assert strict.current() == "key-two-2222"
    assert strict.describe() == bt.BearerStatus(AuthMode.ENV_KEY, None, "2222")
    monkeypatch.delenv("LLM_KEY")
    with pytest.raises(BearerUnavailableError, match="environment variable LLM_KEY is unset"):
        strict.current()
    lenient = EnvironmentKeyBearer("LLM_KEY", required=False)
    assert lenient.current() == "" and lenient.peek() == ""
    assert lenient.describe().last_four == ""


def test_no_bearer_is_the_null_object() -> None:
    bearer = NoBearer()
    assert bearer.current() == "" and bearer.renew() == "" and bearer.peek() == ""
    assert bearer.describe() == bt.BearerStatus(AuthMode.NONE, None, "")


def test_display_url_and_host_strip_credentials_and_query() -> None:
    """AC-36 / H4."""
    assert display_url("http://user:s3cr3tpw@host:8899/t?k=v4lue") == "http://host:8899/t"
    assert display_url("https://llm.internal/v1") == "https://llm.internal/v1"
    assert display_host("http://gpu-box:8000/v1") == "gpu-box:8000"
    assert display_host("https://llm.internal/v1") == "llm.internal"
    assert display_host(None) == "vendor default"
    service = FakeTokenService(["never"], fail_first=3)
    bearer = TokenServiceBearer(
        "http://user:s3cr3tpw@localhost:8899/t?k=v4lue",
        transport=service.transport,
        sleep=lambda _s: None,
    )
    with pytest.raises(TokenServiceError) as excinfo:
        bearer.current()
    assert "s3cr3tpw" not in str(excinfo.value) and "v4lue" not in str(excinfo.value)


def test_last_four_and_redaction() -> None:
    """D4 + H4: last_four is the last four characters; redact_bearer masks the value and any
    'Bearer <x>' pattern."""
    assert last_four_of("tok-one-abcd") == "abcd" and last_four_of("") == ""
    service = FakeTokenService(["tok-one-abcd"])
    bearer = _bearer(service)
    bearer.current()
    text = "Error code: 401 - {'error': 'rejected Bearer tok-one-abcd'} (tok-one-abcd)"
    redacted = redact_bearer(text, bearer)
    assert "tok-one-abcd" not in redacted
    assert "…abcd" in redacted
    assert redact_bearer("Authorization: Bearer some-other-xyz1", NoBearer()) == (
        "Authorization: Bearer …"
    )


def _flow_client(bearer, statuses, recorder: RecordingTransport, first_token: str) -> httpx.Client:
    client = httpx.Client(
        auth=RenewOnStatusAuth(bearer, statuses),
        transport=recorder.transport,
        headers={"Authorization": f"Bearer {first_token}"},
    )
    return client


def test_renew_on_status_auth_renews_and_resends_exactly_once() -> None:
    """R4 at the httpx level: 401 → renew → the SAME request once more with the new bearer;
    a second 401 is returned, not retried again."""
    service = FakeTokenService(["t1", "t2"])
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    recorder = RecordingTransport([401, 200])
    with _flow_client(bearer, (401,), recorder, "t1") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={"q": 1})
    assert response.status_code == 200
    assert recorder.authorizations() == ["Bearer t1", "Bearer t2"]
    assert service.calls == 2
    again = RecordingTransport([401, 401])
    with _flow_client(bearer, (401,), again, "t2") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={"q": 1})
    assert response.status_code == 401 and len(again.requests) == 2
    other = RecordingTransport([403])
    with _flow_client(bearer, (401,), other, "t2") as client:
        assert client.get("http://llm.test/v1/models").status_code == 403
    assert len(other.requests) == 1  # 403 is not in renew_on_status → no renew


def test_renew_on_status_auth_async_flow_matches_sync() -> None:
    service = FakeTokenService(["t1", "t2"])
    bearer = _bearer(service)
    bearer.current()
    recorder = RecordingTransport([401, 200])

    async def go() -> int:
        async with httpx.AsyncClient(
            auth=RenewOnStatusAuth(bearer, (401,)),
            transport=recorder.transport,
            headers={"Authorization": "Bearer t1"},
        ) as client:
            return (await client.post("http://llm.test/v1/chat/completions", json={})).status_code

    assert asyncio.run(go()) == 200
    assert recorder.authorizations() == ["Bearer t1", "Bearer t2"]


def test_failed_renewal_inside_the_flow_returns_the_rejected_response() -> None:
    """Plan deviation D-4: a token service that is down at renew time does not raise out of
    send (the SDK would retry the whole request); the 401 surfaces and last_error is kept."""
    service = FakeTokenService(["t1"], fail_from=2)
    bearer = _bearer(service)
    assert bearer.current() == "t1"
    recorder = RecordingTransport([401, 200])
    with _flow_client(bearer, (401,), recorder, "t1") as client:
        response = client.post("http://llm.test/v1/chat/completions", json={})
    assert response.status_code == 401
    assert len(recorder.requests) == 1
    assert service.calls == 4  # the initial fetch + one bounded renew burst of 3
    assert bearer.last_error is not None and "unreachable after 3 attempts" in bearer.last_error


def test_strip_authorization_auth_removes_the_header() -> None:
    recorder = RecordingTransport([200])
    with httpx.Client(
        auth=StripAuthorizationAuth(),
        transport=recorder.transport,
        headers={"Authorization": "Bearer placeholder"},
    ) as client:
        assert client.get("http://llm.test/v1/models").status_code == 200
    assert recorder.authorizations() == [None]


def test_bearer_rejected_error_carries_last_four_and_detail() -> None:
    error = BearerRejectedError(status=401, host="llm.internal", last_four="abcd")
    assert str(error) == (
        "endpoint llm.internal rejected bearer …abcd (status 401); "
        "renew the token or check ask_your_docs.llm.auth"
    )
    detailed = BearerRejectedError(status=401, host="h", last_four="abcd", detail="down")
    assert str(detailed).endswith("; renew failed: down")


def test_token_never_appears_in_logs(caplog) -> None:
    """AC-9 (bearer half): bearer_fetched / bearer_renewed records carry neither the token
    nor last_four."""
    caplog.set_level(logging.DEBUG)
    service = FakeTokenService(["tok-one-abcd", "tok-two-efgh"])
    bearer = _bearer(service)
    bearer.current()
    bearer.renew("tok-one-abcd", reason="rejected_status")
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "bearer_fetched" in text and "bearer_renewed" in text
    assert "tok-one" not in text and "tok-two" not in text
    assert "abcd" not in text and "efgh" not in text
    assert '"token_url_host": "localhost"' in text


def test_translate_auth_errors_drops_the_body_and_keeps_last_four() -> None:
    """E4 / H4: the SDK's 401/403 — whose body echoes the presented bearer — becomes a
    BearerRejectedError naming host, status, last_four and the failed renewal, never
    the value; the SDK error (and its body) is not chained; other errors pass through."""
    import openai

    service = FakeTokenService(["tok-one-abcd"], fail_from=2)
    bearer = _bearer(service)
    bearer.current()
    with pytest.raises(TokenServiceError):
        bearer.renew("tok-one-abcd", reason="rejected_status")  # leaves last_error behind
    for sdk_error_type, status in (
        (openai.AuthenticationError, 401),
        (openai.PermissionDeniedError, 403),
    ):
        request = httpx.Request("POST", "https://llm.internal/v1/chat/completions")
        response = httpx.Response(
            status, json={"error": "rejected Bearer tok-one-abcd"}, request=request
        )
        sdk_error = sdk_error_type(
            f"Error code: {status} - rejected Bearer tok-one-abcd", response=response, body=None
        )
        with pytest.raises(BearerRejectedError) as excinfo, bt.translate_auth_errors(bearer):
            raise sdk_error
        message = str(excinfo.value)
        assert message.startswith(f"endpoint llm.internal rejected bearer …abcd (status {status})")
        assert (
            "renew failed: token service http://localhost:8899/access-token unreachable" in message
        )
        assert "tok-one" not in message
        assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    with bt.translate_auth_errors(bearer):
        pass  # nothing raised, nothing translated
    with pytest.raises(ValueError, match="not an auth error"), bt.translate_auth_errors(bearer):
        raise ValueError("not an auth error")
