"""Benchmark test autouse fixtures.

Local pytest does not install the ``[fastembed]`` extra, so
``build_embedder(config.embedding)`` — called by the pydocs benchmark
adapter at ``benchmarks/src/benchmarks/eval/systems/pydocs.py`` during
``index()`` — would raise ``OptionalDepMissing`` and break every smoke
test that drives the runner end-to-end.

The production benchmark CI workflow (``.github/workflows/benchmark.yml``)
installs ``pydocs-mcp[dev,fastembed]`` for real sweeps, so this autouse
fixture only intercepts ``build_embedder`` in the **local pytest** path
— CI continues to exercise the real FastEmbed model.

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
