"""Composition root wires LlmClient through BuildContext (AC-10).

Mirrors the embedder + uow_factory threading in ``build_retrieval_context``
/ ``build_ingestion_pipeline``: the entry points (``server.run`` and
``__main__._run_indexing``) construct one LLM client at startup via
``build_llm_client(config.llm)`` and thread it into the BuildContext so
``LlmTreeReasoningStep.from_dict`` can read ``context.llm_client`` instead
of having to import ``build_llm_client`` itself.
"""

from __future__ import annotations

from unittest.mock import patch

from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import FakeLlmClient


def test_build_retrieval_context_threads_llm_client(tmp_path) -> None:
    """``build_retrieval_context`` calls ``build_llm_client(config.llm)``
    and writes the resulting client into ``context.llm_client``."""
    from pydocs_mcp.retrieval import factories

    config = AppConfig.load()
    fake = FakeLlmClient(responses={})
    # Patch at the CONSUMER's import path — ``retrieval/factories.py``
    # imports ``build_llm_client`` at module top so the local binding is
    # what the production code dereferences. Same trick the test for
    # ``build_embedder`` uses elsewhere in this suite.
    with patch.object(factories, "build_llm_client", return_value=fake) as mock:
        ctx = factories.build_retrieval_context(tmp_path / "x.db", config)
    assert ctx.llm_client is fake
    mock.assert_called_once_with(config.llm)


def test_build_ingestion_pipeline_accepts_llm_client_kwarg(tmp_path) -> None:
    """``build_ingestion_pipeline`` accepts an ``llm_client`` kwarg and
    threads it into the BuildContext so ingestion-time LLM stages can
    consume it the same way ``EmbedChunksStage`` consumes ``embedder``.

    Today no shipped ingestion stage reads ``context.llm_client``, but
    the kwarg has to exist symmetrically with the embedder + uow_factory
    + pipeline_hash threading so a future LLM-driven ingestion stage
    slots in without another wiring change.
    """
    from pydocs_mcp.extraction.factories import build_ingestion_pipeline
    from tests._fakes import MockEmbedder, make_fake_uow_factory

    config = AppConfig.load()
    fake = FakeLlmClient(responses={})
    # No assertion on pipeline body — we only care the kwarg is accepted
    # (the signature shape is what the composition roots in __main__.py
    # and server.py depend on). If the kwarg is missing, this call raises
    # TypeError and the test fails loudly. We have to also pass
    # ``uow_factory`` and ``pipeline_hash`` because the shipped ingestion
    # YAML wires stages that require them — without those the build
    # raises ValueError before the test can observe the new kwarg.
    build_ingestion_pipeline(
        config,
        embedder=MockEmbedder(),
        uow_factory=make_fake_uow_factory(),
        pipeline_hash="test-pipeline-hash",
        llm_client=fake,
    )
