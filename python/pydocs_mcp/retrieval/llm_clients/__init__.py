"""LLM client concretes + factory.

The ``LlmClient`` Protocol lives in ``storage/protocols.py`` alongside
the other infrastructure Protocols (``Embedder``, ``UnitOfWork``,
``ChunkStore``, …); concretes implementing it live here under
``retrieval/`` because the only consumer today is the retrieval pipeline
(:class:`pydocs_mcp.retrieval.steps.llm_tree_reasoning.LlmTreeReasoningStep`).

If a future extraction-time consumer lands (e.g., LLM-driven chunk
summarization at index time), the natural move is to lift this package
up to a neutral location (e.g., ``infrastructure/llm_clients/``); for
now the retrieval-only consumer makes the retrieval-owned location the
cleanest dependency direction.

Adding a new provider = one new module + one new branch in
``build_llm_client``.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import LlmConfig
from pydocs_mcp.storage.protocols import LlmClient


def build_llm_client(cfg: LlmConfig) -> LlmClient:
    """Construct the configured LLM client.

    Defers concrete-class imports so server startup doesn't pay both
    cold-import costs upfront. Raises ValueError for unknown providers.
    """
    if cfg.provider == "openai":
        from pydocs_mcp.retrieval.llm_clients.openai import (
            OpenAiLlmClient,
        )
        return OpenAiLlmClient(
            model_name=cfg.model_name,
            api_key=cfg.api_key,
        )
    raise ValueError(
        f"Unknown LLM provider: {cfg.provider!r}. Supported: 'openai'.",
    )


__all__ = ("build_llm_client",)
