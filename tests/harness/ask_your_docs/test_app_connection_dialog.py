"""The status line and the Connection dialog via AppTest (LLM-connection design §4.9 —
AC-25, AC-26, AC-31, AC-35 send-loop half, AC-39 status-line half, AC-43, AC-44, AC-45).

Three session-state seams keep the network out: connection_bearer, connection_list_models,
connection_transport. No test sends a question that builds an agent. A test that clicks
Apply asserts session state right after that run and never chains another run on the same
AppTest (the st.rerun() inside a dialog leaves stale dialog widget state, design §4.9)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

import pydocs_mcp.harness.ask_your_docs.app as appmod
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import TokenServiceBearer
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
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
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import clear_model_listing_cache
from pydocs_mcp.harness.ask_your_docs.multimodal import clear_detection_cache

from ._connection_fakes import (
    FakeBearer,
    FakeClock,
    FakeModelsEndpoint,
    FakeTokenService,
    RecordingTransport,
)

_RENEWED_AT = datetime(2026, 9, 5, 12, 3)
_IDS = ("model-a", "model-b")
_TOKEN_URL = "http://localhost:8899/access-token"


def _write_config(
    tmp_path: Path,
    *,
    base_url="https://llm.internal/v1",
    model=None,
    auth="token",
    vision="true",
) -> str:
    lines = ["ask_your_docs:", "  llm:", f"    base_url: {base_url}"]
    if model:
        lines.append(f"    model: {model}")
    if auth == "token":
        lines += ["    auth:", f"      token_url: {_TOKEN_URL}"]
    elif auth == "env":
        lines += ["    auth:", "      api_key_env: LLM_KEY"]
    if vision is not None:
        lines.append(f"    vision: {vision}")
    path = tmp_path / "pydocs.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _page_env(tmp_path: Path, monkeypatch):
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("PYDOCS_WORKSPACE", str(tmp_path / "ws"))
    for var in ("PYDOCS_CONFIG", "OPENAI_BASE_URL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_KEY"):
        monkeypatch.delenv(var, raising=False)
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()  # the identity-keyed page caches persist across AppTest runs
    yield
    clear_bearer_registry()
    clear_model_listing_cache()
    clear_detection_cache()
    st.cache_resource.clear()


def _app(**seeds) -> AppTest:
    at = AppTest.from_file(appmod.__file__, default_timeout=180)
    at.session_state["connection_list_models"] = seeds.pop("listing", FakeModelsEndpoint(ids=_IDS))
    for key, value in seeds.items():
        at.session_state[key] = value
    return at


def _status_line(at: AppTest) -> str:
    return next(c.value for c in at.caption if " · " in c.value and "vision:" in c.value)


def _open_dialog(at: AppTest) -> AppTest:
    at.run()
    assert not at.exception, at.exception
    at.button(key=KEY_OPEN).click().run()
    assert not at.exception, at.exception
    return at


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
    """AC-25 (c) / E6."""
    at = _open_dialog(_app(listing=FakeModelsEndpoint(error=RuntimeError("boom"))))
    assert at.text_input(key=KEY_MODEL_TEXT).value == "gpt-4o-mini"
    assert any("listing failed: RuntimeError: boom" in c.value for c in at.caption)
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
