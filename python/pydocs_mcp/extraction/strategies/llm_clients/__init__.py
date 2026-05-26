"""LLM client concretes + factory.

Architectural twin of ``embedders/`` — the ``LlmClient`` Protocol lives in
``storage/protocols.py``; concretes implementing it live here. Adding a
new provider = one new module + one new branch in ``build_llm_client``.
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
        from pydocs_mcp.extraction.strategies.llm_clients.openai import (
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
