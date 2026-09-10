"""The E6 shape cases of model discovery (LLM-connection design §4.6 — AC-13's shape half).

Split out of ``test_model_listing.py`` when that module reached its readable-in-one-read
budget: these are the cases where a 200 carries something other than the listing contract,
and every one of them asserts the same thing — the caption names the SHAPE, never the bytes.
"""

from __future__ import annotations

import asyncio
import logging
import warnings

import httpx
import pytest

pytest.importorskip("langchain_openai")

from pydocs_mcp.harness.ask_your_docs import multimodal
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    clear_model_listing_cache,
    fetch_model_ids,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import RecordingTransport

_URL = "http://llm.test/v1"


@pytest.fixture(autouse=True)
def _fresh():
    clear_bearer_registry()
    clear_model_listing_cache()
    multimodal.clear_detection_cache()
    yield
    clear_bearer_registry()
    clear_model_listing_cache()
    multimodal.clear_detection_cache()


def _connection(block: dict):
    cfg = LlmConnectionConfig.model_validate(block)
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
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
