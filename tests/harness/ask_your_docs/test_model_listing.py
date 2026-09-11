"""Model discovery (LLM-connection design §4.6 — AC-12, AC-13, AC-14) and the ladder's
production seams (AC-15 wiring half, AC-16)."""

from __future__ import annotations

import asyncio
import logging
import threading
import warnings
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("langchain_openai")

from pydocs_mcp.harness.ask_your_docs import (
    bearer_tokens,
    llm_connection,
    model_listing,
    multimodal,
)
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerStatus,
    NoBearer,
    TokenServiceBearer,
    TokenServiceError,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    bearer_for_connection,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    cached_model_listing,
    clear_model_listing_cache,
    fetch_model_ids,
    fetch_models_payload,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    AuthMode,
    LlmConnectionConfig,
    MultimodalDetectionConfig,
)

from ._connection_fakes import (
    FakeBearer,
    FakeClock,
    FakeModelsEndpoint,
    FakeTokenService,
    RecordingTransport,
)

_URL = "http://llm.test/v1"
_TOKEN_URL = "http://localhost:8899/access-token"


@pytest.fixture(autouse=True)
def _fresh():
    clear_bearer_registry()
    clear_model_listing_cache()
    multimodal.clear_detection_cache()
    yield
    clear_bearer_registry()
    clear_model_listing_cache()
    multimodal.clear_detection_cache()


def _connection(block: dict | None, env: dict | None = None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(
        cfg, env or {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


def _token_bearer(service: FakeTokenService) -> TokenServiceBearer:
    return TokenServiceBearer(_TOKEN_URL, transport=service.transport, sleep=lambda _s: None)


class _VisionModelsEndpoint(RecordingTransport):
    """A /models endpoint whose one entry carries the vision metadata rung 3 reads."""

    def __call__(self, request):
        self.record(request)  # the parent's snapshot: authorizations() must not pass vacuously
        return httpx.Response(
            200,
            json={"data": [{"id": "my-vlm", "object": "model", "capabilities": {"vision": True}}]},
        )


class _ThreadRecordingBearer:
    """A ``BearerSource`` recording the thread its (blocking) ``current()`` ran on."""

    value = "tok-thread-abcd"

    def __init__(self) -> None:
        self.threads: list[int] = []

    def current(self) -> str:
        self.threads.append(threading.get_ident())
        return self.value

    def peek(self) -> str:
        return self.value

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.ENV_KEY, None, self.value[-4:])


def test_listing_sends_the_bearer_and_sorts_ids() -> None:
    """AC-12: GET /models with the connection's bearer; sorted, deduplicated ids."""
    service = FakeTokenService(["tok-one-abcd"])
    recorder = RecordingTransport([200], model_ids=("zeta", "alpha", "alpha"))
    connection = _connection({"base_url": _URL, "auth": {"token_url": _TOKEN_URL}})
    listing = asyncio.run(
        fetch_model_ids(connection, _token_bearer(service), transport=recorder.transport)
    )
    assert listing == ModelListing(("alpha", "zeta"), None, listing.fetched_at)
    assert recorder.requests[0].url.path.endswith("/models")
    assert recorder.authorizations() == ["Bearer tok-one-abcd"]


def test_listing_without_auth_and_on_the_no_block_path(monkeypatch) -> None:
    """AC-12: NoBearer ⇒ no header; the no-block path works with OPENAI_API_KEY unset (no header,
    no construction error) and carries it when set; base_url=None uses the SDK default path."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)  # the SDK reads it when base_url is None
    recorder = RecordingTransport([200])
    none = _connection({"base_url": _URL})
    asyncio.run(fetch_model_ids(none, NoBearer(), transport=recorder.transport))
    assert recorder.authorizations() == [None]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    unset = RecordingTransport([200])
    no_block = _connection(None, {"OPENAI_BASE_URL": _URL})
    listing = asyncio.run(
        fetch_model_ids(no_block, bearer_for_connection(no_block), transport=unset.transport)
    )
    assert listing.model_ids == ("model-a", "model-b") and unset.authorizations() == [None]
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-9999")
    with_key = RecordingTransport([200])
    asyncio.run(
        fetch_model_ids(no_block, bearer_for_connection(no_block), transport=with_key.transport)
    )
    assert with_key.authorizations() == ["Bearer env-key-9999"]
    vendor_default = RecordingTransport([200])
    asyncio.run(
        fetch_model_ids(
            _connection(None),
            bearer_for_connection(_connection(None)),
            transport=vendor_default.transport,
        )
    )
    assert vendor_default.requests[0].url.path == "/v1/models"


def test_listing_failures_are_non_fatal(caplog) -> None:
    """AC-13 / E6: 5xx, a connection error and an id-less payload become ModelListing(error=…)
    with one model_listing_failed log; bearer failures propagate instead."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})
    down = RecordingTransport([500, 500])  # the listing client retries once
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=down.transport))
    assert listing.model_ids == () and "InternalServerError" in (listing.error or "")
    # Two scripted errors for the same reason: the SDK retries a connection error too.
    refused = RecordingTransport([httpx.ConnectError("refused"), httpx.ConnectError("refused")])
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=refused.transport))
    assert listing.error is not None and listing.error.startswith("APIConnectionError")
    no_ids = FakeModelsEndpoint(entry={"name": "x"})
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), list_models=no_ids))
    assert (
        listing.error
        == "unexpected /models payload: expected {'data': [{'id': ...}]}, got keys=['name']"
    )
    # Three, not two: the shape path logs the same structured record as the network paths.
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 3
    with pytest.raises(TokenServiceError):
        asyncio.run(
            fetch_model_ids(
                connection, FakeBearer(fail=True), list_models=FakeModelsEndpoint(ids=("a",))
            )
        )


class _EchoingErrorEndpoint(RecordingTransport):
    """A /models endpoint whose error body echoes a token the bearer has already renewed away.

    The shape a gateway really sends: the rejected credential quoted BARE, with no
    ``Bearer `` prefix in front of it, inside prose the endpoint chose.
    """

    def __init__(self, status: int, body_text: str) -> None:
        super().__init__()
        self.status = status
        self.body_text = body_text

    def __call__(self, request):
        self.record(request)
        return httpx.Response(self.status, json={"error": {"message": self.body_text}})


def test_a_failure_caption_never_carries_the_upstream_body(caplog) -> None:
    """H4: the caption and the log name the failure's CLASS and STATUS, never ``str(exc)``.

    The SDK puts the response body in its message, and ``redact_bearer`` cannot save it:
    it masks the bearer's CURRENT value plus a ``Bearer <x>`` pattern, so a body echoing a
    token the bearer has since renewed away — quoted bare — survives verbatim into both the
    dialog's caption and the WARNING record.
    """
    caplog.set_level(logging.WARNING)
    stale = "tok-stale-1111"
    connection = _connection({"base_url": _URL})
    endpoint = _EchoingErrorEndpoint(500, f"rejected {stale} for tenant acme")
    bearer = FakeBearer("tok-fresh-9999")  # already renewed: peek() is the NEW token
    listing = asyncio.run(fetch_model_ids(connection, bearer, transport=endpoint.transport))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert listing.error == "InternalServerError (status 500)"
    for leak in (stale, "tenant acme", "rejected", "Error code"):
        assert leak not in (listing.error or "") and leak not in logged
    assert "InternalServerError (status 500)" in logged


def test_a_transport_failure_captions_the_class_alone() -> None:
    """A connection error carries no status, so the caption is the class name by itself —
    ``APIConnectionError (status None)`` would read as a status the endpoint never sent."""
    connection = _connection({"base_url": _URL})
    dial = httpx.ConnectError("dial tcp 10.0.0.1:443: refused")
    refused = RecordingTransport([dial, dial])  # the listing client retries once
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=refused.transport))
    assert listing.error == "APIConnectionError"


def test_the_listing_and_the_ladder_share_one_bearer_error_tuple() -> None:
    """H3: bearer failures must re-raise at BOTH sites, so the tuple and the seam type have one
    home — a fourth bearer error added to a private copy would be swallowed by the other."""
    assert model_listing.BEARER_ERRORS is bearer_tokens.BEARER_ERRORS
    assert multimodal.BEARER_ERRORS is bearer_tokens.BEARER_ERRORS
    assert model_listing.ListModels is multimodal.ListModels
    assert set(bearer_tokens.BEARER_ERRORS) == {
        bearer_tokens.TokenServiceError,
        bearer_tokens.BearerUnavailableError,
        bearer_tokens.BearerRejectedError,
    }


def test_listing_cache_ttl_and_eviction() -> None:
    """AC-14: one fetch inside the TTL, a second past it, clear_model_listing_cache evicts."""
    clock = FakeClock()
    connection = _connection({"base_url": _URL})
    endpoint = FakeModelsEndpoint(ids=("model-a",))

    def listing() -> ModelListing:
        return asyncio.run(
            cached_model_listing(connection, NoBearer(), now=clock, list_models=endpoint)
        )

    assert listing().model_ids == ("model-a",)
    assert listing().model_ids == ("model-a",)
    assert endpoint.calls == 1
    clock.advance(model_listing._MODEL_LISTING_TTL_SECONDS + 1)
    listing()
    assert endpoint.calls == 2
    clear_model_listing_cache(connection)
    listing()
    assert endpoint.calls == 3
    other = _connection({"base_url": "http://other/v1"})
    asyncio.run(cached_model_listing(other, NoBearer(), now=clock, list_models=endpoint))
    assert endpoint.calls == 4  # keyed on (base_url, identity)


def test_a_bearer_failure_inside_the_cache_propagates_and_caches_nothing() -> None:
    """H3 at the cache boundary: ``cached_model_listing`` must let a bearer error out and store
    nothing. A cached ``ModelListing(error=…)`` would outlive the renewal that fixes it, so the
    dialog would keep showing a stale auth failure for the whole TTL."""
    connection = _connection({"base_url": _URL})
    endpoint = FakeModelsEndpoint(ids=("a",))
    with pytest.raises(TokenServiceError):
        asyncio.run(cached_model_listing(connection, FakeBearer(fail=True), list_models=endpoint))
    assert model_listing._listing_cache == {}
    assert endpoint.calls == 1  # the seam ran; the bearer failed inside it


def test_rung_three_default_seam_goes_through_the_listing(monkeypatch) -> None:
    """AC-15 (wiring half): _default_list_models is fetch_models_payload(connection, bearer)."""
    seen: list[tuple] = []

    async def _spy(connection, bearer, /):  # positional-only: a keyword call fails the pin
        seen.append((connection, bearer))
        return [{"id": "my-vlm", "capabilities": {"vision": True}}]

    monkeypatch.setattr(model_listing, "fetch_models_payload", _spy)
    connection = _connection(
        {"base_url": _URL, "model": "my-vlm", "auth": {"token_url": _TOKEN_URL}}
    )
    bearer = FakeBearer("tok-fixed-abcd")
    payload = asyncio.run(multimodal._default_list_models(connection, bearer))
    assert payload[0]["id"] == "my-vlm" and seen == [(connection, bearer)]


def test_rung_three_reaches_the_real_listing_over_a_mock_transport(monkeypatch) -> None:
    """AC-15 end to end: with NO injected rung seam, detect_capabilities runs the production
    chain — _default_list_models → fetch_models_payload → openai.AsyncOpenAI. The rung seam
    takes no transport, so the listing's httpx-client helper is where the fake enters; every
    other link is the real one."""
    recorder = _VisionModelsEndpoint()
    build_client = model_listing.async_httpx_client
    monkeypatch.setattr(
        model_listing, "async_httpx_client", lambda auth, _t: build_client(auth, recorder.transport)
    )
    connection = _connection({"base_url": _URL, "model": "my-vlm"})
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    caps = asyncio.run(
        multimodal.detect_capabilities(
            "my-vlm", _URL, cfg, connection=connection, bearer=NoBearer()
        )
    )
    assert caps == multimodal.ModelCapabilities(True, multimodal.CapabilitySource.ENDPOINT)
    assert recorder.requests[0].url.path.endswith("/models")


def test_rung_four_default_seam_builds_through_the_factory(monkeypatch) -> None:
    """AC-16: the probe model is built by build_chat_model with max_retries=0 and the probe timeout."""
    seen: list[dict] = []

    class _SpyLlm:
        async def ainvoke(self, messages):
            return SimpleNamespace(content="OK")

    def _spy_build(connection, bearer, **kwargs):
        seen.append(kwargs)
        return _SpyLlm()

    monkeypatch.setattr(llm_connection, "build_chat_model", _spy_build)
    connection = _connection(
        {"base_url": _URL, "model": "my-vlm", "auth": {"token_url": _TOKEN_URL}}
    )
    reply = asyncio.run(multimodal._default_probe_llm(connection, FakeBearer(), "my-vlm", 5.0))
    assert reply == "OK"
    assert seen == [{"model": "my-vlm", "timeout_seconds": 5.0, "max_retries": 0}]


def test_fetch_models_payload_keeps_the_extra_fields() -> None:
    """Rung 3 keeps reading the metadata fields it reads today (_entry_hints_vision)."""
    recorder = _VisionModelsEndpoint()
    connection = _connection({"base_url": _URL})
    payload = asyncio.run(
        fetch_models_payload(connection, NoBearer(), transport=recorder.transport)
    )
    assert payload == [{"id": "my-vlm", "object": "model", "capabilities": {"vision": True}}]
    assert recorder.authorizations() == [None]  # NoBearer ⇒ the header never reached the wire


def test_the_listing_runs_a_sync_key_callable_off_the_event_loop_thread() -> None:
    """The factory hands the SDK ``bearer.current``, whose first call may block on a bounded
    token fetch — the async wrapper must run it on a worker thread (test_sdk_pins' fifth pin)."""
    bearer = _ThreadRecordingBearer()
    connection = _connection({"base_url": _URL, "auth": {"api_key_env": "PYDOCS_TEST_KEY"}})
    recorder = RecordingTransport([200])

    async def go() -> int:
        await fetch_model_ids(connection, bearer, transport=recorder.transport)
        return threading.get_ident()

    loop_thread = asyncio.run(go())
    assert bearer.threads and all(thread != loop_thread for thread in bearer.threads)
    assert recorder.authorizations() == ["Bearer tok-thread-abcd"]


# ── the LiteLLM probe and the listing's entries (model-params v2 §2, D10) ──

import json

import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs import litellm_probe

from ._connection_fakes import FakeModelGroupInfo

_LOGGER = "pydocs-mcp.harness.ask-your-docs"


@pytest.fixture(autouse=True)
def _fresh_probe():
    litellm_probe.clear_litellm_probe_cache()
    yield
    litellm_probe.clear_litellm_probe_cache()


def _events(caplog, event: str) -> list[dict]:
    lines = [json.loads(r.getMessage()) for r in caplog.records if event in r.getMessage()]
    return [line for line in lines if line.get("event") == event]


def _probe(connection, bearer=None, entry=None, **kw):
    return asyncio.run(
        litellm_probe.litellm_group_row(connection, bearer or NoBearer(), entry, **kw)
    )


def test_the_listing_keeps_its_entries_by_id_as_the_last_field() -> None:
    endpoint = FakeModelsEndpoint(entry=FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B"))
    connection = _connection({"base_url": _URL})
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), list_models=endpoint))
    assert listing.entries_by_id["Qwen/Qwen3-8B"]["owned_by"] == "vllm"
    assert [f for f in ModelListing.__dataclass_fields__][-1] == "entries_by_id"
    assert ModelListing((), None, 0.0).entries_by_id == {}
    assert listing == ModelListing(("Qwen/Qwen3-8B",), None, listing.fetched_at)


def test_group_info_200_detects_litellm_once_and_caches(caplog) -> None:
    info = FakeModelGroupInfo("team-sonnet")
    connection = _connection({"base_url": _URL, "model": "team-sonnet"})
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert _probe(connection, group_info=info) == info.row()
        assert _probe(connection, group_info=info) == info.row()
    assert info.calls == 1  # the second call is a cache hit
    (detected,) = _events(caplog, "litellm_detected")
    assert "drop_params" in detected["note"] and _events(caplog, "litellm_probe_failed") == []
    other_model = _connection({"base_url": _URL, "model": "not-a-group"})
    assert _probe(other_model, group_info=info) == {}  # LiteLLM, no row for this model


def test_group_info_404_is_generic_with_one_body_free_log_line(caplog) -> None:
    recorder = RecordingTransport([404])
    connection = _connection({"base_url": _URL, "model": "m", "auth": {"token_url": _TOKEN_URL}})
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        assert (
            _probe(connection, FakeBearer("tok-fixed-abcd"), transport=recorder.transport) is None
        )
    (request,) = recorder.requests
    assert request.url.path == "/model_group/info"  # base_url minus its trailing /v1
    assert recorder.authorizations() == ["Bearer tok-fixed-abcd"]  # the listing's auth path
    (failed,) = _events(caplog, "litellm_probe_failed")
    assert failed["status"] == 404
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "abcd" not in text and "rejected" not in text


def test_group_info_retries_once_then_stops(monkeypatch, caplog) -> None:
    monkeypatch.setattr(sdk_base, "INITIAL_RETRY_DELAY", 0.0)
    monkeypatch.setattr(sdk_base, "MAX_RETRY_DELAY", 0.0)
    connection = _connection({"base_url": _URL, "model": "m"})
    for script, status in (([500, 500, 500], 500), ([httpx.ReadTimeout("slow")] * 3, None)):
        litellm_probe.clear_litellm_probe_cache()
        caplog.clear()
        recorder = RecordingTransport(list(script))
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            assert _probe(connection, transport=recorder.transport) is None
        assert len(recorder.requests) == 2  # one bounded retry
        assert [e["status"] for e in _events(caplog, "litellm_probe_failed")] == [status]


def test_the_probe_is_skipped_once_the_profile_is_decided() -> None:
    info = FakeModelGroupInfo("m")
    decided = [
        (_connection({"base_url": "https://openrouter.ai/api/v1", "model": "m"}), None),
        (_connection({"base_url": _URL, "model": "m", "provider": "vllm"}), None),
        (_connection({"base_url": _URL, "model": "m", "provider": "generic"}), None),
        (_connection({"model": "m"}), None),  # null base_url = the vendor default (openai)
        (_connection({"base_url": _URL, "model": "m"}), FakeModelsEndpoint.vllm_entry("m")),
    ]
    for connection, entry in decided:
        assert _probe(connection, entry=entry, group_info=info) is None
    assert info.calls == 0
