"""SentenceTransformersEmbedder tests.

All tests inject a fake ``model`` (small object with ``encode_query`` /
``encode_document`` returning canned np arrays) so NO torch / sentence-
transformers load is needed — they run in the default ``.venv``.
"""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
    SentenceTransformersEmbedder,
)

_DIM = 8


class _FakeModel:
    """Records call args + returns deterministic, distinguishable vectors.

    ``encode_query`` and ``encode_document`` return different leading
    components so a test can assert the query path went through
    ``encode_query`` (the asymmetric prompt path) and the document path
    through ``encode_document``.
    """

    def __init__(self) -> None:
        self.max_seq_length = 0
        self.query_calls: list[dict] = []
        self.document_calls: list[dict] = []

    def encode_query(self, sentences, **kwargs):
        self.query_calls.append({"sentences": sentences, **kwargs})
        out = np.zeros((len(sentences), _DIM), dtype=np.float64)
        out[:, 0] = 1.0  # marker: query path
        return out

    def encode_document(self, sentences, **kwargs):
        self.document_calls.append({"sentences": sentences, **kwargs})
        out = np.zeros((len(sentences), _DIM), dtype=np.float64)
        out[:, 1] = 1.0  # marker: document path
        return out


@pytest.fixture
def emb() -> SentenceTransformersEmbedder:
    return SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=_FakeModel())


def test_dim_field_exposed(emb: SentenceTransformersEmbedder) -> None:
    assert emb.dim == _DIM
    assert emb.model_name == "x"


async def test_embed_query_uses_encode_query_path(emb: SentenceTransformersEmbedder) -> None:
    v = await emb.embed_query("the query")
    # Asymmetric query path: must have gone through encode_query.
    assert len(emb.model.query_calls) == 1
    assert emb.model.document_calls == []
    assert v[0] == 1.0  # query marker component
    assert isinstance(v, np.ndarray)
    assert v.dtype == np.float32
    assert v.shape == (_DIM,)


async def test_embed_query_is_model_agnostic_no_prompt_by_default(
    emb: SentenceTransformersEmbedder,
) -> None:
    # Model-agnostic default: no prompt_name is forced, so a model without a
    # named query prompt is not pushed through a non-existent prompt (which
    # would raise). encode_query still applies the model's OWN default prompt.
    assert emb.query_prompt_name is None
    await emb.embed_query("q")
    call = emb.model.query_calls[0]
    assert "prompt_name" not in call
    assert call.get("normalize_embeddings") is True
    assert call.get("convert_to_numpy") is True


async def test_embed_query_passes_prompt_name_when_configured() -> None:
    # An explicit override IS forwarded — for an asymmetric model where the
    # caller wants a specific named prompt.
    emb = SentenceTransformersEmbedder(
        model_name="x", dim=_DIM, model=_FakeModel(), query_prompt_name="query"
    )
    await emb.embed_query("q")
    assert emb.model.query_calls[0].get("prompt_name") == "query"


async def test_embed_chunks_uses_encode_document_path(
    emb: SentenceTransformersEmbedder,
) -> None:
    out = await emb.embed_chunks(["a", "bb", "ccc"])
    assert len(emb.model.document_calls) == 1
    assert emb.model.query_calls == []
    assert len(out) == 3
    assert all(isinstance(v, np.ndarray) for v in out)
    assert all(v.dtype == np.float32 for v in out)
    assert all(v.shape == (_DIM,) for v in out)
    # Document path marker is component 1.
    assert all(v[1] == 1.0 for v in out)


async def test_embed_chunks_passes_batch_size(emb: SentenceTransformersEmbedder) -> None:
    await emb.embed_chunks(["a", "b"])
    call = emb.model.document_calls[0]
    assert call.get("normalize_embeddings") is True
    assert call.get("convert_to_numpy") is True
    assert call.get("batch_size") == emb.batch_size


async def test_embed_chunks_empty_returns_empty_tuple(
    emb: SentenceTransformersEmbedder,
) -> None:
    assert await emb.embed_chunks([]) == ()
    # No model call at all on the empty path.
    assert emb.model.document_calls == []


def test_close_nulls_model_and_is_idempotent(emb: SentenceTransformersEmbedder) -> None:
    assert emb.model is not None
    emb.close()
    assert emb.model is None
    # Idempotent: a second close on an already-closed embedder does not raise.
    emb.close()
    assert emb.model is None


def test_accepts_device_field() -> None:
    e = SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=_FakeModel(), device="cuda")
    assert e.device == "cuda"


def test_injected_model_skips_real_load() -> None:
    """An injected model means __post_init__ must NOT import sentence_transformers."""
    fake = _FakeModel()
    e = SentenceTransformersEmbedder(model_name="x", dim=_DIM, model=fake)
    # __post_init__ should still apply the seq-length cap to the injected model.
    assert e.model is fake
    assert fake.max_seq_length == e.max_seq_length
