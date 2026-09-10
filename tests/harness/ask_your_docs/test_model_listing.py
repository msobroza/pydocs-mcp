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


class _RawBodyEndpoint(RecordingTransport):
    """A /models endpoint answering 200 with an arbitrary top-level body (E6 shape cases)."""

    def __init__(self, body: dict) -> None:
        super().__init__()
        self.body = body

    def __call__(self, request):
        self.record(request)
        return httpx.Response(200, json=self.body)


class _NonObjectBodyEndpoint(RecordingTransport):
    """A /models endpoint answering 200 with a body that is not a JSON object at all.

    Not expressible as ``_RawBodyEndpoint``'s ``dict``: these are the raw bytes
    plus the content type a real deployment sends — a bare array, the HTML a
    wrong ``base_url`` serves, an empty body — and the SDK raises while PARSING
    each one, inside ``models.list()``.
    """

    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__()
        self.body = body
        self.content_type = content_type

    def __call__(self, request):
        self.record(request)
        return httpx.Response(200, content=self.body, headers={"content-type": self.content_type})


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


def _listing_over(connection, body: dict) -> ModelListing:
    """One ``fetch_model_ids`` against a 200 carrying ``body`` (the E6 shape cases)."""
    endpoint = _RawBodyEndpoint(body)
    return asyncio.run(fetch_model_ids(connection, NoBearer(), transport=endpoint.transport))


def _listing_over_bytes(
    connection, body: bytes, content_type: str = "application/json"
) -> ModelListing:
    """One ``fetch_model_ids`` against a 200 whose body is raw bytes (the non-object E6 cases)."""
    endpoint = _NonObjectBodyEndpoint(body, content_type)
    return asyncio.run(fetch_model_ids(connection, NoBearer(), transport=endpoint.transport))


def test_a_200_whose_body_is_not_an_object_names_what_came_back(caplog) -> None:
    """E6: the SDK builds the page INSIDE ``models.list()``, so a top-level body that is not a
    JSON object raises there — before any ``page.data`` guard can see it. A bare array, the HTML
    a wrong ``base_url`` serves, a JSON scalar and an empty body must all caption the expected
    SHAPE and what came back, never the SDK's own AttributeError / JSONDecodeError text."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})
    array = _listing_over_bytes(connection, b'[{"id": "a"}]')
    html = _listing_over_bytes(connection, b"<html><body>Sign in</body></html>", "text/html")
    empty = _listing_over_bytes(connection, b"")
    number = _listing_over_bytes(connection, b"5")
    prefix = "unexpected /models payload: expected {'data': [{'id': ...}]}, got "
    assert array == ModelListing((), f"{prefix}a list body", array.fetched_at)
    assert html == ModelListing((), f"{prefix}a str body", html.fetched_at)
    assert empty == ModelListing((), f"{prefix}an empty body", empty.fetched_at)
    assert number.error == f"{prefix}an int body"
    captions = " ".join(listing.error or "" for listing in (array, html, empty, number))
    for leak in ("AttributeError", "JSONDecodeError", "_set_private_attributes", "Sign in"):
        assert leak not in captions
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 4


def test_a_listing_under_a_wrong_content_type_still_reads() -> None:
    """The body guard mirrors the SDK's own parse, which tries JSON whatever the content type
    says — so a proxy answering ``text/html`` with a REAL listing must not be rejected as a
    shape error. Over-rejecting here would caption a healthy endpoint as broken."""
    connection = _connection({"base_url": _URL})
    listing = _listing_over_bytes(connection, b'{"data": [{"id": "z"}]}', "text/html")
    assert listing == ModelListing(("z",), None, listing.fetched_at)


def test_a_200_without_a_data_key_names_the_body_keys(caplog) -> None:
    """E6: a 200 whose body carries no ``data`` list is a SHAPE caption naming the keys the
    endpoint did send — never the raw TypeError of iterating ``None``."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})
    empty = _listing_over(connection, {}).error
    assert empty == "unexpected /models payload: expected {'data': [{'id': ...}]}, got body keys=[]"
    wrong_key = _listing_over(connection, {"models": [{"id": "a"}]}).error
    assert wrong_key == (
        "unexpected /models payload: expected {'data': [{'id': ...}]}, got body keys=['models']"
    )
    not_a_list = _listing_over(connection, {"data": "nope"}).error
    assert not_a_list == (
        "unexpected /models payload: expected {'data': [{'id': ...}]}, "
        "got body keys=['data'], data of type str"
    )
    assert not any("TypeError" in (error or "") for error in (empty, wrong_key, not_a_list))
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 3


def test_blank_entry_ids_read_differently_from_a_missing_id_key() -> None:
    """The shape caption distinguishes entries that carry no ``id`` KEY from entries whose ids
    are present but empty — ``got keys=['id']`` would contradict itself."""
    connection = _connection({"base_url": _URL})
    blank = _RawBodyEndpoint({"data": [{"id": ""}, {"id": ""}]})
    listing = asyncio.run(fetch_model_ids(connection, NoBearer(), transport=blank.transport))
    assert listing.error == (
        "unexpected /models payload: expected {'data': [{'id': ...}]}, "
        "got entries whose ids are empty"
    )
    assert blank.authorizations() == [None]


def test_entries_that_are_not_objects_name_their_type(caplog) -> None:
    """E6: a listing whose entries are bare values reports the SHAPE the endpoint sent — the
    conversion never lets a Python attribute error reach the caption."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})
    strings = _listing_over(connection, {"data": ["model-a", "model-b"]})
    nulls = _listing_over(connection, {"data": [None]})
    nested = _listing_over(connection, {"data": [[1, 2]]})
    prefix = "unexpected /models payload: expected {'data': [{'id': ...}]}, got "
    assert strings.error == f"{prefix}str entries"
    assert nulls.error == f"{prefix}NoneType entries"
    assert nested.error == f"{prefix}list entries"
    assert all(listing.model_ids == () for listing in (strings, nulls, nested))
    captions = " ".join(listing.error or "" for listing in (strings, nulls, nested))
    assert "AttributeError" not in captions and "to_dict" not in captions
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 3


def test_a_non_string_id_reads_the_same_under_warnings_as_errors() -> None:
    """The gate runs ``-W error``; production does not. A non-string ``id`` must caption
    identically in both — never the SDK's pydantic serializer warning."""
    connection = _connection({"base_url": _URL})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")  # production mode: a warning is a warning
        numeric = _listing_over(connection, {"data": [{"id": 5}]})
        mixed = _listing_over(connection, {"data": [{"id": 5}, {"id": "ok"}]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # gate mode: a warning would become the caption
        strict = _listing_over(connection, {"data": [{"id": 5}]})
    assert numeric == ModelListing(
        (),
        "unexpected /models payload: expected {'data': [{'id': ...}]}, got ids of type int",
        numeric.fetched_at,
    )
    assert strict.error == numeric.error
    assert mixed == ModelListing(("ok",), None, mixed.fetched_at)  # one bad entry, a live listing
    assert [str(w.message) for w in caught] == []


def test_a_seam_payload_that_is_not_a_listing_becomes_a_caption(caplog) -> None:
    """The ids are derived inside the same caption path as the fetch: an injected seam that
    answers something other than a list of entries fails soft with a SHAPE caption — never
    raising into the dialog and never captioning a bare KeyError/TypeError."""
    caplog.set_level(logging.WARNING)
    connection = _connection({"base_url": _URL})

    async def _mapping_seam(_connection, _bearer, /):
        return {"data": [{"id": "a"}]}  # a mapping, not the entries the seam owes

    async def _nothing_seam(_connection, _bearer, /):
        return None

    mapping = asyncio.run(fetch_model_ids(connection, NoBearer(), list_models=_mapping_seam))
    nothing = asyncio.run(fetch_model_ids(connection, NoBearer(), list_models=_nothing_seam))
    prefix = "unexpected /models payload: expected {'data': [{'id': ...}]}, got "
    assert mapping == ModelListing((), f"{prefix}dict payload", mapping.fetched_at)
    assert nothing == ModelListing((), f"{prefix}NoneType payload", nothing.fetched_at)
    assert len([r for r in caplog.records if "model_listing_failed" in r.getMessage()]) == 2


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
