"""Benchmark test autouse fixtures.

``fastembed`` is now a required dep (the shipped default config selects
``provider=fastembed``), so ``build_embedder(config.embedding)`` — called
by the pydocs benchmark adapter at
``benchmarks/src/benchmarks/eval/systems/pydocs.py`` during ``index()`` —
imports successfully. The catch: constructing ``FastEmbedEmbedder``
triggers a ~80MB ONNX model download on first inference, which would
balloon local pytest runtime and require network access.

This autouse fixture intercepts ``build_embedder`` in the **local pytest**
path with a lightweight mock so every smoke test that drives the runner
end-to-end stays fast and offline. The production benchmark CI workflow
(``.github/workflows/benchmark.yml``) runs against the real FastEmbed
model.

The mock is intentionally a minimal duplicate of
``tests/_fakes.MockEmbedder``: the benchmarks subproject has its own
``pyproject.toml`` with ``testpaths = ["tests"]``, which makes pytest
rootdir-detect at ``benchmarks/`` and resolve ``tests`` to
``benchmarks/tests/`` — colliding with the repo-root ``tests`` package
import. A small local class avoids that namespace clash without
needing custom rootdir / pythonpath plumbing.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pytest


@dataclass(frozen=True, slots=True)
class _BenchmarkMockEmbedder:
    """Deterministic Embedder test double for benchmark smoke tests.

    Mirrors ``tests/_fakes.MockEmbedder``'s shape (``embed_query`` /
    ``embed_chunks`` / ``model_name`` / ``dim``) so it's a drop-in for
    the production ``Embedder`` Protocol used by the ingestion pipeline.
    """

    dim: int = 384
    # Mirrors the ``Embedder`` Protocol's ``model_name`` field — written
    # into ``Package.embedding_model`` by ``EmbedChunksStage`` so any
    # benchmark assertions on the persisted model name see a stable value.
    model_name: str = "mock"

    async def embed_query(self, text: str) -> np.ndarray:
        return self._derive(text)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[np.ndarray, ...]:
        return tuple(self._derive(t) for t in texts)

    def _derive(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "little", signed=False)
        rng = np.random.default_rng(seed)
        return rng.uniform(-1.0, 1.0, size=self.dim).astype(np.float32)


@pytest.fixture(autouse=True)
def _patch_build_embedder_with_mock(monkeypatch):
    """Patch ``build_embedder`` so benchmark smoke tests don't need fastembed.

    Mirrors the ``tests/test_cli.py::_patch_embedder_with_mock`` pattern:
    the shipped default config selects ``provider=fastembed``, but the
    local test venv installs only the base deps. Patching at the call-site
    module (``embedders``) covers every consumer that does
    ``from pydocs_mcp.extraction.strategies.embedders import build_embedder``
    — including the benchmark adapter's ``index()`` path.
    """
    mock = _BenchmarkMockEmbedder()
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.embedders.build_embedder",
        lambda cfg: mock,
    )
    yield


@dataclass(slots=True)
class _BenchmarkFakeLlmClient:
    """No-op LlmClient stand-in for benchmark smoke tests.

    The retrieval-side composition root (``build_retrieval_context``) now
    eagerly calls ``build_llm_client(config.llm)`` so the resulting client
    lives on ``BuildContext.llm_client``. The shipped default config picks
    ``provider=openai`` and instantiating ``AsyncOpenAI`` requires
    ``OPENAI_API_KEY`` — which the local benchmark test env doesn't set.
    Patching at the canonical module attribute AND the rebound name on
    ``retrieval.factories`` covers both call sites (mirrors the embedder
    pattern and the same-name autouse in ``tests/conftest.py``).

    Trivial chat() implementation — none of the benchmark smoke tests
    drive ``LlmTreeReasoningStep``, so the fake never actually services
    a request; the constructor existing is the entire contract.
    """

    model_name: str = "benchmark-fake-llm"

    async def chat(self, messages, **kwargs) -> str:
        raise NotImplementedError(
            "_BenchmarkFakeLlmClient.chat called — benchmark tests must "
            "not exercise the LLM tree-reasoning path",
        )

    def chat_sync(self, messages, **kwargs) -> str:
        raise NotImplementedError(
            "_BenchmarkFakeLlmClient.chat_sync called — benchmark tests "
            "must not exercise the LLM tree-reasoning path",
        )


@pytest.fixture(autouse=True)
def _patch_build_llm_client_with_fake(monkeypatch):
    """Patch ``build_llm_client`` so benchmark smoke tests stay offline.

    Mirrors the matching fixture in ``tests/conftest.py``. Without this,
    ``build_retrieval_context`` raises ``openai.OpenAIError`` on every
    benchmark run that touches the retrieval composition.
    """
    fake = _BenchmarkFakeLlmClient()

    def _factory(cfg):
        return fake

    monkeypatch.setattr(
        "pydocs_mcp.retrieval.llm_clients.build_llm_client",
        _factory,
    )
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.factories.build_llm_client",
        _factory,
    )
    yield
