"""OpenAIEmbedder on the HTTP wire, through the real openai SDK.

The sibling ``test_openai_embedder.py`` swaps the whole ``openai`` module for a
mock, so it cannot see what the SDK sends or how it decodes the reply. Here the
embedder is built for real — its constructor reads the key from the variable
``api_key_env`` names, so a synthetic value is set there first — and its eagerly
built ``_client`` is then replaced with an SDK client on
:class:`~tests._fakes.FakeOpenAIWire` (``httpx.MockTransport``).

Kind: characterization — green on openai 1.109.1 and on 2.x.
"""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.extraction.strategies.embedders.openai import OpenAIEmbedder
from tests._fakes import FakeOpenAIWire

_DIM = 4


def _wired_embedder(
    monkeypatch: pytest.MonkeyPatch, wire: FakeOpenAIWire, *, send_dimensions: bool = True
) -> OpenAIEmbedder:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    embedder = OpenAIEmbedder(
        model_name="text-embedding-3-small", dim=_DIM, send_dimensions=send_dimensions
    )
    embedder._client = wire.async_openai()
    return embedder


async def test_dimensions_is_sent_when_send_dimensions_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    await _wired_embedder(monkeypatch, wire).embed_query("hello")
    (request,) = wire.requests
    assert request.path == "/v1/embeddings"
    assert request.body["dimensions"] == _DIM
    assert request.body["model"] == "text-embedding-3-small"


async def test_dimensions_is_omitted_when_send_dimensions_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Endpoints such as codestral-embed via OpenRouter reject the key outright,
    # so it must be absent — not sent as null.
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    await _wired_embedder(monkeypatch, wire, send_dimensions=False).embed_query("hello")
    (request,) = wire.requests
    assert "dimensions" not in request.body


async def test_query_vector_is_float32_decoded_from_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    # The embedder passes no encoding_format, so the SDK asks for base64 and
    # decodes it; the adapter must still hand back float32.
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    vector = await _wired_embedder(monkeypatch, wire).embed_query("hello")
    (request,) = wire.requests
    assert request.body["encoding_format"] == "base64"
    assert vector.dtype == np.float32
    np.testing.assert_array_equal(vector, wire.vector_for("hello"))


async def test_chunk_vectors_come_back_as_float32_in_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    texts = ["alpha", "beta", "gamma"]
    vectors = await _wired_embedder(monkeypatch, wire).embed_chunks(texts)
    (request,) = wire.requests
    assert request.body["input"] == texts
    assert [v.dtype for v in vectors] == [np.float32] * len(texts)
    for text, vector in zip(texts, vectors, strict=True):
        np.testing.assert_array_equal(vector, wire.vector_for(text))


async def test_empty_batch_sends_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    assert await _wired_embedder(monkeypatch, wire).embed_chunks([]) == ()
    assert wire.requests == []


async def test_fake_wire_serves_float_lists_when_float_encoding_is_asked() -> None:
    # Keeps the fake honest on its other branch: a caller that asks for float
    # gets plain lists, which the SDK hands back undecoded.
    wire = FakeOpenAIWire(embedding_dim=_DIM)
    response = await wire.async_openai().embeddings.create(
        model="text-embedding-3-small", input=["alpha"], encoding_format="float"
    )
    assert response.data[0].embedding == [float(x) for x in wire.vector_for("alpha")]


def test_real_sdk_client_takes_the_configured_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # The unpatched construction path, for an OpenAI-compatible endpoint whose
    # key lives in its own variable.
    monkeypatch.setenv("PYDOCS_TEST_EMBEDDINGS_KEY", "sk-test-embeddings")
    embedder = OpenAIEmbedder(
        model_name="mistralai/codestral-embed-2505",
        dim=1536,
        base_url="https://embeddings.example.test/api/v1",
        api_key_env="PYDOCS_TEST_EMBEDDINGS_KEY",
        send_dimensions=False,
    )
    assert str(embedder._client.base_url) == "https://embeddings.example.test/api/v1/"
    assert embedder._client.api_key == "sk-test-embeddings"
