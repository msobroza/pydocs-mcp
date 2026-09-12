"""embedding.query_prefix through the real composition roots.

Query side (server/CLI/benchmark retrieval) must carry the prefix; the
write side (ingestion) and the late-interaction query side must never see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context, build_shared_retrieval_deps
from tests._fakes import FakeLlmClient, RecordingEmbedder

_PREFIX = "Instruct: find the code\nQuery:"
_CORE_PY = '"""Core module."""\n\n\ndef core_fn():\n    """Core."""\n    return 1\n'


@pytest.fixture
def prefixed_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    # Env var path — the documented non-YAML way to set the knob.
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_PREFIX", _PREFIX)
    return AppConfig.load()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, prefixed_config: AppConfig) -> RecordingEmbedder:
    recording = RecordingEmbedder(dim=prefixed_config.embedding.dim)
    monkeypatch.setattr("pydocs_mcp.retrieval.factories.build_embedder", lambda cfg: recording)
    return recording


async def test_build_shared_retrieval_deps_uses_build_query_embedder(
    prefixed_config: AppConfig, provider: RecordingEmbedder
) -> None:
    embedder = build_shared_retrieval_deps(prefixed_config)[0]
    await embedder.embed_query("q")
    assert provider.query_texts == [_PREFIX + "q"]


async def test_build_retrieval_context_uses_build_query_embedder(
    prefixed_config: AppConfig, provider: RecordingEmbedder, tmp_path: Path
) -> None:
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    ctx = build_retrieval_context(db_path, prefixed_config)
    assert ctx.embedder is not None
    await ctx.embedder.embed_query("q")
    assert provider.query_texts == [_PREFIX + "q"]


@dataclass(slots=True)
class _RecordingMultiVectorEmbedder:
    dim: int = 8
    model_name: str = "fake-colbert"
    query_texts: list[str] = field(default_factory=list)

    async def embed_query(self, text: str) -> list[np.ndarray]:
        self.query_texts.append(text)
        return [np.ones(self.dim, dtype=np.float32)]

    async def embed_chunks(self, texts: list[str]) -> tuple[list[np.ndarray], ...]:
        return tuple([np.ones(self.dim, dtype=np.float32)] for _ in texts)


async def test_multi_vector_embedder_unaffected_by_query_prefix(
    prefixed_config: AppConfig, provider: RecordingEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Late interaction is out of scope: ColBERT's 32-token query budget would
    # be mostly consumed by an instruction, and its identity lives elsewhere.
    recording_mv = _RecordingMultiVectorEmbedder()
    monkeypatch.setattr(
        "pydocs_mcp.retrieval.factories.build_multi_vector_embedder", lambda cfg: recording_mv
    )
    multi_vector = build_shared_retrieval_deps(prefixed_config)[1]
    assert multi_vector is not None
    await multi_vector.embed_query("q")
    assert recording_mv.query_texts == ["q"]


async def test_ingestion_embedder_never_prefixed(
    prefixed_config: AppConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import llm_clients as _llm_clients
    from pydocs_mcp.storage.factories import build_project_indexer

    ingestion = RecordingEmbedder(dim=prefixed_config.embedding.dim)
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: ingestion)
    monkeypatch.setattr(_llm_clients, "build_llm_client", lambda cfg: FakeLlmClient(responses={}))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "core.py").write_text(_CORE_PY)
    db_path = tmp_path / "proj_abc123.db"
    open_index_database(db_path).close()
    bundle = build_project_indexer(prefixed_config, db_path, use_inspect=False, inspect_depth=None)
    await bundle.orchestrator.index_project(
        project, force=False, include_project_source=True, include_dependencies=False, workers=1
    )
    embedded = [text for batch in ingestion.chunk_batches for text in batch]
    assert embedded, "the tiny corpus must reach the ingestion embedder"
    assert ingestion.query_texts == []
    assert not any("Instruct:" in text for text in embedded)


async def test_dense_fetcher_query_carries_prefix_end_to_end(
    prefixed_config: AppConfig, provider: RecordingEmbedder, tmp_path: Path
) -> None:
    # The retrieval step reads the embedder from the ambient BuildContext,
    # so decoding it proves the prefix reaches the real dense query path.
    from pydocs_mcp.models import SearchQuery
    from pydocs_mcp.retrieval.pipeline import RetrieverState
    from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep

    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    ctx = build_retrieval_context(db_path, prefixed_config)
    step = DenseFetcherStep.from_dict({}, ctx)
    await step.run(RetrieverState(query=SearchQuery(terms="  q ", max_results=5)))
    assert provider.query_texts == [_PREFIX + "q"]
