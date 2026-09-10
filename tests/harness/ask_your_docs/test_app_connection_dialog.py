"""The status line and the Connection dialog via AppTest (LLM-connection design §4.9 —
AC-25, AC-26, AC-31, AC-35 send-loop half, AC-39 status-line half, AC-43, AC-44, AC-45).

Three session-state seams keep the network out: connection_bearer, connection_list_models,
connection_transport. No test sends a question that builds an agent. A test that clicks
Apply asserts session state right after that run and never chains another run on the same
AppTest (the st.rerun() inside a dialog leaves stale dialog widget state, design §4.9)."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx
import pytest

pytest.importorskip("streamlit")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import TokenServiceBearer
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    BEARER_WARN,
    CLEARTEXT_NOTE,
    KEY_APPLY,
    KEY_BASE_URL,
    KEY_MODEL,
    KEY_MODEL_TEXT,
    KEY_OPEN,
    KEY_REFRESH,
    KEY_RENEW,
    KEY_TEST,
    NOTHING_RENEWED,
    ORIGIN_NOTE,
    STATE_OVERRIDE,
    STATE_TEST_RESULT,
    TOKEN_UNAVAILABLE,
)
from pydocs_mcp.harness.ask_your_docs.cli import LAUNCH_BASE_URL_ENV_VAR
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride

from ._connection_fakes import (
    FakeBearer,
    FakeClock,
    FakeModelsEndpoint,
    FakeTokenService,
    RecordingTransport,
)

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import (
    MODEL_IDS as _IDS,
)
from ._page_fixtures import (
    PAGE_LOGGER as _PAGE_LOGGER,
)
from ._page_fixtures import (
    TOKEN_URL as _TOKEN_URL,
)
from ._page_fixtures import open_dialog as _open_dialog
from ._page_fixtures import page as _app
from ._page_fixtures import page_env, status_line as _status_line
from ._page_fixtures import write_config as _write_config

_RENEWED_AT = datetime(2026, 9, 5, 12, 3)


def _seed_agent_failing_in_ask(monkeypatch, error: Exception) -> None:
    """Drive the send loop to ``ask`` without a network: build hands back a stub ``(agent, llm)``
    pair, reformulate is the identity and ask raises ``error``. AppTest re-executes app.py on
    every run, so the page's ``from … import`` picks these up."""

    async def build_agent(*args, **kwargs):
        return object(), object()

    async def reformulate(_llm, _history, question, **kwargs):
        return question

    async def ask(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent_module, "build_agent", build_agent)
    monkeypatch.setattr(agent_module, "ask", ask)
    monkeypatch.setattr(reformulation_module, "reformulate", reformulate)


def _sdk_authentication_error() -> Exception:
    """The SDK's 401 the way a gateway sends it: the body echoes the presented bearer (AC-35)."""
    import openai

    request = httpx.Request("POST", "https://llm.internal/v1/chat/completions")
    body = {"error": {"message": "rejected Bearer tok-one-abcd", "type": "auth"}}
    return openai.AuthenticationError(
        f"Error code: 401 - {body}",
        response=httpx.Response(401, json=body, request=request),
        body=None,
    )


def test_status_line_and_dialog_for_the_no_block_default() -> None:
    """AC-25 (a)(b): the no-block page renders host, model, auth and the vision verdict on one
    caption; the dialog carries Base URL, the seeded model ids and Apply."""
    at = _app()
    at.run()
    assert not at.exception, at.exception
    assert _status_line(at) == "vendor default · gpt-4o-mini · no auth · vision: yes (static)"
    _open_dialog(at)
    assert at.text_input(key=KEY_BASE_URL).value == ""
    assert list(at.selectbox(key=KEY_MODEL).options) == list(_IDS)
    assert at.selectbox(key=KEY_MODEL).value == "model-a"  # the default id is not listed ⇒ first id
    assert at.button(key=KEY_APPLY) is not None
    assert not [b for b in at.button if b.key == KEY_RENEW]  # no token service ⇒ no Renew (d)


def test_apply_writes_the_session_override_and_the_next_page_reads_it() -> None:
    """AC-25 (e) / AC-26: Apply stores a ConnectionOverride; a fresh page resolves it (session
    only, never persisted)."""
    at = _open_dialog(_app())
    at.text_input(key=KEY_BASE_URL).input("http://other/v1")
    at.selectbox(key=KEY_MODEL).select("model-b")
    at.button(key=KEY_APPLY).click().run()
    assert at.session_state[STATE_OVERRIDE] == ConnectionOverride(
        base_url="http://other/v1", model="model-b"
    )
    fresh = _app(
        **{STATE_OVERRIDE: ConnectionOverride(base_url="http://other/v1", model="model-b")}
    )
    fresh.run()
    assert _status_line(fresh).startswith("other · model-b · ")


def test_listing_failure_falls_back_to_a_text_field() -> None:
    """AC-25 (c) / E6 — and H4 at the page: the caption names the failure's CLASS alone.

    ``boom`` stands in for the response body the SDK carries in its message; the dialog
    must never quote it, because a real one echoes the credential the endpoint rejected.
    """
    at = _open_dialog(_app(listing=FakeModelsEndpoint(error=RuntimeError("boom"))))
    assert at.text_input(key=KEY_MODEL_TEXT).value == "gpt-4o-mini"
    captions = [c.value for c in at.caption]
    assert any("listing failed: RuntimeError" in caption for caption in captions)
    assert not any("boom" in caption for caption in captions)
    assert not [s for s in at.selectbox if s.key == KEY_MODEL]


def test_invalid_base_url_is_a_caption_not_a_crash() -> None:
    """A typed base URL urlsplit rejects (``http://[bad``) is named in a caption; nothing is
    listed, tested or applied, and the page never raises on user input."""
    at = _open_dialog(_app())
    at.text_input(key=KEY_BASE_URL).input("http://[bad").run()
    assert not at.exception, at.exception
    assert any("invalid base URL 'http://[bad'" in c.value for c in at.caption)
    assert not [b for b in at.button if b.key in (KEY_APPLY, KEY_TEST)]
    assert not [s for s in at.selectbox if s.key == KEY_MODEL]


def test_token_service_cells_inline_last_four_and_renewal_time(tmp_path, monkeypatch) -> None:
    """AC-44 (a)(d) / AC-25 (d)(f): token …abcd 12:03 inline on the status line; the dialog's
    auth row shows both; Renew is present; model: not chosen opens the selectbox on the first id."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path))
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    assert not at.exception, at.exception
    assert (
        _status_line(at)
        == "llm.internal · model: not chosen · token …abcd 12:03 · vision: yes (configured)"
    )
    at.button(key=KEY_OPEN).click().run()
    assert any(c.value == "token …abcd · renewed 12:03" for c in at.caption)
    assert at.button(key=KEY_RENEW) is not None
    assert at.selectbox(key=KEY_MODEL).value == "model-a"


def test_no_model_under_detect_has_no_verdict(tmp_path, monkeypatch) -> None:
    """vision: null with no model chosen yet: there is nothing to detect, so the cell is ``?``
    rather than the ladder's text-only default for an empty name."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, vision=None))
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    assert not at.exception, at.exception
    assert _status_line(at).endswith("model: not chosen · token …abcd 12:03 · vision: ?")


def test_separate_vision_model_shows_the_vision_half(tmp_path, monkeypatch) -> None:
    """Under vision: {model: …} the main verdict is deliberately blind; the badge (and the
    send policy behind it) read the VISION half, so the line never says 'vision: no'."""
    monkeypatch.setenv(
        "PYDOCS_CONFIG", _write_config(tmp_path, model="main-a", vision="{model: vision-b}")
    )
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    assert not at.exception, at.exception
    assert _status_line(at).endswith("main-a · token …abcd 12:03 · vision: yes (configured)")


def test_token_unavailable_is_visible_and_builds_nothing(tmp_path, monkeypatch) -> None:
    """AC-44 (b) / E1 / H3: a token service that is down shows on the status line at render."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    at = _app(connection_bearer=FakeBearer(fail=True))
    at.run()
    assert not at.exception, at.exception
    assert TOKEN_UNAVAILABLE in _status_line(at)
    assert "vision: ?" in _status_line(at)  # no verdict is computed on an unavailable bearer


def test_environment_key_cells(tmp_path, monkeypatch) -> None:
    """AC-44 (c): $VAR missing / $VAR set for an explicit auth.api_key_env."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a", auth="env"))
    at = _app()
    at.run()
    assert "$LLM_KEY missing" in _status_line(at)
    monkeypatch.setenv("LLM_KEY", "key-one-1111")
    again = _app()
    again.run()
    assert "$LLM_KEY set" in _status_line(again)


def test_origin_change_and_cleartext_notes(tmp_path, monkeypatch) -> None:
    """AC-31 / AC-39 (status-line halves): an override on another plain-http origin ends the auth
    cell with the origin note, preceded by ⚠ http; the configured https origin shows neither."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gpu-box:8000/v1")
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    line = _status_line(at)
    assert line.startswith("gpu-box:8000 · main-a · token …abcd 12:03 ")
    assert line.split(" · vision:")[0].endswith(ORIGIN_NOTE)
    assert f"{CLEARTEXT_NOTE} {ORIGIN_NOTE}" in line
    monkeypatch.delenv("OPENAI_BASE_URL")
    clean = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    clean.run()
    assert ORIGIN_NOTE not in _status_line(clean) and CLEARTEXT_NOTE not in _status_line(clean)


def test_launcher_flag_beats_openai_base_url_on_the_status_line(tmp_path, monkeypatch) -> None:
    """0.6.1: --base-url reaches the page under a private name as the CLI tier of spec R3, so
    it still wins over OPENAI_BASE_URL (which the serve child now inherits untouched)."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    monkeypatch.setenv(LAUNCH_BASE_URL_ENV_VAR, "http://gpu-box:8000/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://other:9000/v1")
    at = _app(connection_bearer=FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT))
    at.run()
    assert _status_line(at).startswith("gpu-box:8000")


def test_test_connection_passes_fails_redacted_and_follows_the_endpoint(
    tmp_path, monkeypatch
) -> None:
    """AC-43 (a)(b)(c) / E11."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    ok = RecordingTransport([200], reply="OK")
    at = _open_dialog(
        _app(connection_bearer=FakeBearer("tok-fixed-abcd"), connection_transport=ok.transport)
    )
    at.button(key=KEY_TEST).click().run()
    assert at.session_state[STATE_TEST_RESULT] == "test passed: OK"
    assert ok.authorizations() == ["Bearer tok-fixed-abcd"]
    # Two 401s: the renewing Auth re-sends once after the first (R4), so a single scripted
    # rejection would be answered 200 on the re-send and the test would PASS.
    rejected = RecordingTransport([401, 401], echo_bearer_in_401=True)
    at = _open_dialog(
        _app(
            connection_bearer=FakeBearer("tok-fixed-abcd"), connection_transport=rejected.transport
        )
    )
    at.button(key=KEY_TEST).click().run()
    result = at.session_state[STATE_TEST_RESULT]
    assert result.startswith("test failed: BearerRejectedError:")
    assert "tok-fixed-abcd" not in result and "rejected Bearer" not in result
    elsewhere = RecordingTransport([200], reply="OK")
    at = _open_dialog(
        _app(
            connection_bearer=FakeBearer("tok-fixed-abcd"),
            connection_transport=elsewhere.transport,
        )
    )
    at.text_input(key=KEY_BASE_URL).input("https://gpu-box:8443/v1")
    at.button(key=KEY_TEST).click().run()
    assert elsewhere.authorizations() == [
        "Bearer tok-fixed-abcd"
    ]  # the bearer follows the endpoint (D3)
    assert str(elsewhere.requests[0].url).startswith("https://gpu-box:8443/v1")
    assert at.session_state[STATE_TEST_RESULT].endswith(ORIGIN_NOTE)


def test_refresh_and_renew_evict_the_listing(tmp_path, monkeypatch) -> None:
    """AC-45: refresh re-lists; Renew renews the bearer once and evicts the listing entry."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    endpoint = FakeModelsEndpoint(ids=_IDS)
    bearer = FakeBearer("tok-fixed-abcd", renewed_at=_RENEWED_AT)
    at = _open_dialog(_app(listing=endpoint, connection_bearer=bearer))
    assert endpoint.calls == 1
    at.button(key=KEY_REFRESH).click().run()
    assert endpoint.calls == 2
    renewing = _open_dialog(_app(listing=endpoint, connection_bearer=bearer))
    assert endpoint.calls == 2  # inside the TTL: the cached listing served the dialog
    renewing.button(key=KEY_RENEW).click().run()
    assert bearer.renewals == 1
    assert endpoint.calls == 3  # the entry was evicted, the reopened dialog listed again


def test_renew_inside_the_rate_limit_says_nothing_was_renewed(tmp_path, monkeypatch) -> None:
    """H3 through the page: a manual Renew the real bearer answers from its cache (a renewal
    younger than its interval) is reported as such, and the listing entry stays put."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    service = FakeTokenService(["tok-one-abcd", "tok-two-efgh"])
    bearer = TokenServiceBearer(_TOKEN_URL, transport=service.transport, now=FakeClock())
    bearer.current()  # the first fetch
    bearer.renew(reason="manual")  # the renewal the page's click will be measured against
    assert service.calls == 2
    endpoint = FakeModelsEndpoint(ids=_IDS)
    at = _open_dialog(_app(listing=endpoint, connection_bearer=bearer))
    assert endpoint.calls == 1
    at.button(key=KEY_RENEW).click().run()
    assert not at.exception, at.exception
    assert at.session_state[STATE_TEST_RESULT] == NOTHING_RENEWED
    assert service.calls == 2 and endpoint.calls == 1  # no fetch, no eviction
    assert bearer.peek() == "tok-two-efgh"


def test_send_loop_boundary_renders_a_redacted_error_and_keeps_the_question(
    tmp_path, monkeypatch
) -> None:
    """AC-35 (send-loop half): an auth failure reaches the page as st.error through redact_bearer
    plus the kept question — here the E1 leg (the 401 leg is pinned in test_chat_model_factory)."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    bearer = FakeBearer(
        "tok-fixed-abcd",
        fail=True,
        fail_message="token service down; last sent Bearer tok-fixed-abcd",
    )
    at = _app(connection_bearer=bearer)
    at.run()
    at.chat_input[0].set_value("what does Pool.acquire return?").run()
    assert not at.exception, at.exception
    errors = [e.value for e in at.error]
    assert errors and "tok-fixed-abcd" not in errors[0] and "…abcd" in errors[0]
    assert any(
        "Your question (not sent): what does Pool.acquire return?" in i.value for i in at.info
    )


def test_send_loop_boundary_catches_a_failure_of_any_type(tmp_path, monkeypatch, caplog) -> None:
    """The boundary is EVERY failure, not a tuple of known classes: a plain RuntimeError carrying
    the bearer becomes a redacted st.error with the question kept, never Streamlit's exception
    box (which would print the token unredacted). Its ONE log record is a WARNING (an INFO would
    be dropped under `streamlit run`) carrying the exception class alone — H4 on logs."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    _seed_agent_failing_in_ask(monkeypatch, RuntimeError("upstream rejected Bearer tok-one-abcd"))
    at = _app(connection_bearer=FakeBearer("tok-one-abcd"))
    at.run()
    with caplog.at_level(logging.WARNING, logger=_PAGE_LOGGER):
        at.chat_input[0].set_value("what does Pool.acquire return?").run()
    assert not at.exception, at.exception
    errors = [e.value for e in at.error]
    assert errors == ["RuntimeError: upstream rejected Bearer …abcd"]
    assert any(
        "Your question (not sent): what does Pool.acquire return?" in i.value for i in at.info
    )
    logged = [r for r in caplog.records if r.name == _PAGE_LOGGER]
    assert [json.loads(r.getMessage()) for r in logged] == [
        {"event": "send_failed", "error": "RuntimeError"}
    ]
    assert "tok-one-abcd" not in caplog.text and "upstream rejected" not in caplog.text


def test_send_loop_translates_the_sdk_401_without_its_body(tmp_path, monkeypatch) -> None:
    """AC-35 (send-loop half, the 401 leg): reformulate and ask run inside translate_auth_errors,
    so the SDK's 401 — whose body echoes the presented credential — reaches the page as the
    redacted rejection, with the last four and no body at all."""
    monkeypatch.setenv("PYDOCS_CONFIG", _write_config(tmp_path, model="main-a"))
    _seed_agent_failing_in_ask(monkeypatch, _sdk_authentication_error())
    at = _app(connection_bearer=FakeBearer("tok-one-abcd"))
    at.run()
    at.chat_input[0].set_value("what does Pool.acquire return?").run()
    assert not at.exception, at.exception
    errors = [e.value for e in at.error]
    assert errors and errors[0].startswith(
        "BearerRejectedError: endpoint llm.internal rejected bearer …abcd (status 401)"
    )
    assert "tok-one-abcd" not in errors[0] and "Error code: 401" not in errors[0]


def test_a_probe_bearer_failure_keeps_the_page_and_the_connection_button(
    tmp_path, monkeypatch
) -> None:
    """E5 under the opt-in endpoint probe: the ladder fetches the bearer at render, and an unset
    auth.api_key_env must land on the status line — not in Streamlit's exception box, where the
    Connection dialog that would fix it is unreachable. The cell keeps AC-44 (c)'s wording for the
    variable and only gains the mark, so the same unset key reads the same with and without the
    probe (`$LLM_KEY missing` here vs test_environment_key_cells)."""
    monkeypatch.setenv(
        "PYDOCS_CONFIG",
        _write_config(tmp_path, model="mystery-1", auth="env", vision=None, endpoint_probe=True),
    )
    at = _app()
    at.run()
    assert not at.exception, at.exception
    assert f"$LLM_KEY missing {BEARER_WARN}" in _status_line(at)
    assert TOKEN_UNAVAILABLE not in _status_line(at) and "vision: ?" in _status_line(at)
    assert any("LLM_KEY" in (c.proto.help or "") for c in at.caption)  # the redacted E5 text
    assert at.button(key=KEY_OPEN) is not None
