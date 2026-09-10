"""query_prefix reaches the REAL openai / fastembed provider request.

Both providers go through the QueryPrefixEmbedder wrapper, so these build the
genuine chain via ``build_query_embedder`` (``real_embedder`` opts out of the
autouse MockEmbedder patch) with only the third-party SDK faked, and assert
the exact text in the outgoing request: queries prefixed, documents never.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from pydocs_mcp.retrieval.config import EmbeddingConfig, QueryCacheConfig
from pydocs_mcp.retrieval.factories import build_query_embedder

_PREFIX = "Instruct: find the code\nQuery:"
_NO_CACHE = QueryCacheConfig(enabled=False)
_OPENAI_MODULE = "pydocs_mcp.extraction.strategies.embedders.openai"
_FASTEMBED_MODULE = "pydocs_mcp.extraction.strategies.embedders.fastembed"


class _FakeTextEmbedding:
    """Stands in for fastembed.TextEmbedding; records every embed() batch."""

    batches: list[list[str]] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).batches = []

    def embed(self, texts: list[str]) -> Iterator[np.ndarray]:
        type(self).batches.append(list(texts))
        return iter([np.ones(384, dtype=np.float32) for _ in texts])


@pytest.fixture
def fresh_provider_modules() -> Iterator[None]:
    # The provider modules bind the SDK at import, so drop them before and
    # after each test to import them against the faked SDK.
    for name in (_OPENAI_MODULE, _FASTEMBED_MODULE):
        sys.modules.pop(name, None)
    yield
    for name in (_OPENAI_MODULE, _FASTEMBED_MODULE):
        sys.modules.pop(name, None)


def _openrouter_qwen() -> EmbeddingConfig:
    return EmbeddingConfig(
        provider="openai",
        model_name="qwen/qwen3-embedding-4b",
        dim=2560,
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        send_dimensions=False,
        query_prefix=_PREFIX,
        query_cache=_NO_CACHE,
    )


@pytest.mark.real_embedder
@pytest.mark.usefixtures("fresh_provider_modules")
async def test_openai_request_input_prefixed_for_queries_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-not-a-key")
    with patch.dict(sys.modules, {"openai": MagicMock()}):
        chain = build_query_embedder(_openrouter_qwen())
    provider = chain.inner  # type: ignore[attr-defined]
    fake_resp = MagicMock(data=[MagicMock(embedding=[0.1, 0.2])])
    provider._client.embeddings.create = AsyncMock(return_value=fake_resp)
    await chain.embed_query("  q \n")
    await chain.embed_chunks(["doc"])
    inputs = [c.kwargs["input"] for c in provider._client.embeddings.create.call_args_list]
    assert inputs == [_PREFIX + "q", ["doc"]]


@pytest.mark.real_embedder
@pytest.mark.usefixtures("fresh_provider_modules")
async def test_fastembed_embed_input_prefixed_for_queries_only() -> None:
    fake_fastembed = MagicMock(TextEmbedding=_FakeTextEmbedding)
    with patch.dict(sys.modules, {"fastembed": fake_fastembed}):
        chain = build_query_embedder(EmbeddingConfig(query_prefix=_PREFIX, query_cache=_NO_CACHE))
    await chain.embed_query("  q \n")
    await chain.embed_chunks(["doc"])
    assert _FakeTextEmbedding.batches == [[_PREFIX + "q"], ["doc"]]
