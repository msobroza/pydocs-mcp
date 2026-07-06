"""Retrieval-pipeline protocols — cross-cutting structural types.

Persistence contracts live in :mod:`pydocs_mcp.storage.protocols`; this
module owns the contracts whose implementations and consumers are
retrieval- / extraction-side:

- :class:`ConnectionProvider` — the SQLite-connection acquisition contract
  threaded through ``BuildContext`` into the fetcher steps.
- :class:`ResultFormatter` — the per-item render contract used by
  ``application/formatting`` and the token-budget step.
- :class:`Embedder` / :class:`MultiVectorEmbedder` — embedding contracts
  consumed by the dense retrieval steps and the ingestion embed stages.
- :class:`ChatMessage` / :class:`LlmClient` — LLM chat contracts; concretes
  live in :mod:`pydocs_mcp.retrieval.llm_clients`.
- :class:`ResultFuser` — rank-fusion contract (``RRFResultFuser``).

``RetrieverStep`` (the nominal ABC every step subclasses) lives in
:mod:`pydocs_mcp.retrieval.pipeline.base`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, runtime_checkable

from pydocs_mcp.models import Chunk, Embedding, ModuleMember

if TYPE_CHECKING:
    import numpy as np


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation.

    Two acquisition surfaces:

    - :meth:`acquire` — async context manager, the canonical entry point
      for the retrieval pipeline (where steps are ``async def`` and can
      ``await`` directly).
    - :meth:`acquire_sync` — sync context manager (spec C4). Retrieval
      steps that hand work off to ``asyncio.to_thread`` (CPU-bound
      fetches; SQLite I/O on the executor) cannot nest an ``async with``
      inside the worker thread without re-entering the event loop, so
      they call ``acquire_sync()`` to obtain a connection opened with
      ``check_same_thread=False``.
    """

    # WHY AbstractAsyncContextManager (not AsyncIterator): every caller uses
    # ``async with provider.acquire()``, and ``@asynccontextmanager``-decorated
    # implementations return an async CM. The old AsyncIterator annotation
    # misdescribed that contract; the mismatch was masked while
    # ``build_connection_provider`` was untyped (implicit Any) and surfaced
    # when the factory gained its concrete return type.
    def acquire(self) -> AbstractAsyncContextManager[sqlite3.Connection]: ...

    def acquire_sync(self) -> AbstractContextManager[sqlite3.Connection]:
        """Sync acquire — yields a ``sqlite3.Connection`` from a ``with`` block.

        Returned connection MUST be opened with
        ``check_same_thread=False`` so callers can use it inside an
        ``asyncio.to_thread`` worker (the executor thread differs from
        the thread that opened the connection). Closing happens on
        ``__exit__``. Implementations typically build the CM via a
        ``@contextmanager``-decorated generator that yields a single
        connection (see :class:`PerCallConnectionProvider.acquire_sync`).
        """
        ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""

    def format(self, result: Chunk | ModuleMember) -> str: ...


@runtime_checkable
class Embedder(Protocol):
    """One embedder serves both query-time and ingestion-time work.

    Spec §5.2 — concrete classes return their natural shape:
    single-vector embedders (FastEmbed, OpenAI, BGE) return ``np.ndarray``
    (1D, float32); future ColBERT-style embedders return ``MultiVector``
    (list of 1D ``np.ndarray``\\ s). Use
    `pydocs_mcp.models.is_multi_vector(emb)` to disambiguate.
    """

    # Defaults make the attributes discoverable via hasattr(Embedder, ...)
    # for structural / introspection tests. Real implementations override.
    dim: int = 0
    # Identifier string the embedder embedded with — written to
    # ``Package.embedding_model`` by ``EmbedChunksStage`` so a YAML
    # ``embedding.model_name`` swap triggers the re-embed sweep in
    # :meth:`IndexingService.invalidate_stale_embeddings`.
    model_name: str = ""

    async def embed_query(self, text: str) -> Embedding: ...

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]: ...


@runtime_checkable
class MultiVectorEmbedder(Protocol):
    """Late-interaction (ColBERT-style) embedder: one vector PER TOKEN.

    Distinct from :class:`Embedder` (single pooled vector per text).
    ``embed_query`` / ``embed_chunks`` each return a
    ``MultiVector = list[np.ndarray]`` of length ``n_tokens`` — every
    element is a 1-D float32 ``np.ndarray`` of length ``dim``. The outer
    container is a Python ``list`` (NOT a stacked 2-D array) because
    :func:`pydocs_mcp.models.is_multi_vector` disambiguates the
    ``Embedding`` union via ``isinstance(emb, list)``.

    Implementations MUST L2-normalize each token-vector before returning
    so MaxSim's downstream dot-product IS the cosine — no per-query
    renormalization in ``_maxsim`` (spec Decision C).
    """

    dim: int
    model_name: str

    async def embed_query(self, text: str) -> list[np.ndarray]: ...

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[list[np.ndarray], ...]: ...


class ChatMessage(TypedDict):
    """One message in an LLM chat-completion conversation.

    Mirrors the OpenAI / Anthropic / common LLM API shape: role +
    content. Used by LlmClient.chat() / chat_sync() as input.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LlmClient(Protocol):
    """LLM chat-completion client.

    Exposes BOTH async ``chat()`` and sync ``chat_sync()`` — LLM calls
    surface in more contexts than embedding calls (the MCP server is
    async, but the CLI debug path, test helpers, and notebooks need a
    sync surface).

    Implementations live under
    ``python/pydocs_mcp/retrieval/llm_clients/``. The factory
    ``build_llm_client(cfg)`` dispatches on ``cfg.provider`` to the
    right concrete (OpenAiLlmClient for v1; SOLID open/closed for
    future providers). Protocol and concretes are both retrieval-owned
    because the only consumer today is LlmTreeReasoningStep; if
    extraction-time LLM use lands, both can be lifted to a neutral
    location together.
    """

    model_name: str

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Async chat completion. Returns the assistant's response text."""
        ...

    def chat_sync(
        self,
        messages: Sequence[ChatMessage],
        *,
        response_format: Literal["text", "json_object"] = "text",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Sync chat completion. Same contract as ``chat()``."""
        ...


@runtime_checkable
class ResultFuser(Protocol):
    """Combines N ranked Chunk lists into one fused ranking.

    Spec §5.2. Implementations: RRFResultFuser (reciprocal-rank fusion).
    Future: WeightedSumResultFuser, DistributionBasedResultFuser.
    """

    async def fuse(
        self,
        ranked_lists: Sequence[tuple[Chunk, ...]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]: ...
