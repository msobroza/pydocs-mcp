"""Neuledge Context adapter (spec §4.10).

Hosts both the async HTTP client (``NeuledgeClient`` / ``NeuledgeError``)
and the ``NeuledgeSystem`` adapter in one file. The client has exactly
one consumer — this System — so the previous
``benchmarks/neuledge_client.py`` module was a ceremonial split. They
live together here because they're one cohesive concern: a thin wrapper
around the local Neuledge Context MCP server.

The server is expected to be running at ``base_url`` (default
``http://localhost:8080/mcp``) and already has the library indexed —
``index`` only establishes the MCP session, and ``search`` issues
``get_docs(library, topic)``. Like Context7, Neuledge returns a single
concatenated doc blob per query, so we emit one rank-1 ``RetrievedItem``.

Neuledge Context is a local-first documentation MCP server backed by
SQLite FTS5. It exposes a ``get_docs`` tool with ``library`` and
``topic`` parameters. The server must be running locally::

    context serve --http 8080

This client connects via MCP Streamable HTTP at
``http://localhost:8080/mcp``. The server uses Server-Sent Events (SSE)
format for responses and requires an MCP initialize handshake before
tool calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..gold_resolver import DEFAULT_FUZZ_THRESHOLD, LazyFuzzyGoldResolver
from ..registries import system_registry
from ._mcp_http import _DEFAULT_TIMEOUT, McpHttpClient
from .base_system import RetrievedItem, single_blob_items

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from ..gold_resolver import GoldResolver


class NeuledgeError(Exception):
    """Raised when a Neuledge Context MCP call fails."""


def _parse_sse_json(text: str) -> dict:
    """Extract the JSON-RPC result from an SSE response body.

    Neuledge returns responses as SSE events:
        event: message
        data: {"result": {...}, "jsonrpc": "2.0", "id": 1}

    We extract the JSON from the `data:` line.
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            return json.loads(payload)
    # Fallback: try parsing the whole body as JSON
    return json.loads(text)


class NeuledgeClient(McpHttpClient):
    """Async context-manager client for Neuledge Context MCP tools.

    Usage::

        async with NeuledgeClient() as client:
            docs = await client.get_docs("pandas", "DataFrame merge")
    """

    error_cls = NeuledgeError

    def __init__(
        self,
        base_url: str = "http://localhost:8080/mcp",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, timeout)
        self._session_id: str | None = None

    async def __aenter__(self) -> NeuledgeClient:
        # WHY the override: Neuledge requires an MCP initialize handshake
        # before any tool call (Context7 is sessionless and skips it).
        await super().__aenter__()
        await self._initialize()
        return self

    def _headers(self) -> dict[str, str]:
        # WHY: Neuledge's Streamable HTTP transport is session-ful — echo
        # the Mcp-Session-Id captured during initialize on every request.
        if self._session_id:
            return {"Mcp-Session-Id": self._session_id}
        return {}

    def _decode(self, resp: httpx.Response) -> dict:
        return _parse_sse_json(resp.text)

    def _extract_text(self, result: dict) -> str:
        # Join ALL text blocks — Neuledge may split docs across blocks and
        # interleave non-text content (filtered out here).
        content = result.get("content", [])
        texts = [c["text"] for c in content if c.get("type") == "text"]
        return "\n".join(texts)

    async def _initialize(self) -> None:
        """Send MCP initialize handshake (required before tool calls)."""
        assert self._http is not None
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pyctx7-bench", "version": "1.0"},
            },
        }
        resp = await self._http.post(self._base_url, json=payload)
        resp.raise_for_status()

        # Store session ID from Mcp-Session-Id header if present
        self._session_id = resp.headers.get("mcp-session-id")

        data = _parse_sse_json(resp.text)
        if "error" in data:
            raise NeuledgeError(f"MCP initialize error: {data['error']}")

        # Send initialized notification
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        await self._http.post(self._base_url, json=notif, headers=headers)

    async def get_docs(self, library: str, topic: str) -> str:
        """Search documentation for a library by topic.

        Args:
            library: Package identifier as shown in `context list`
                     (e.g. "pandas@2.2.2", "numpy@2.0.0").
            topic: Search query / topic string.

        Returns:
            Documentation text from Neuledge Context.
        """
        return await self.call_tool(
            "get_docs",
            {
                "library": library,
                "topic": topic,
            },
        )


@system_registry.register("neuledge")
@dataclass
class NeuledgeSystem:
    """Adapter for the local Neuledge Context MCP server."""

    name: str = "neuledge"
    base_url: str = "http://localhost:8080/mcp"
    # WHY: set per task — the runner is expected to seed ``library`` to the
    # ``name@version`` identifier returned by ``context list``. Empty
    # default keeps construction cheap (and registry-buildable).
    library: str = ""
    _client: NeuledgeClient | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        if self._client is None:
            self._client = NeuledgeClient(base_url=self.base_url)
            await self._client.__aenter__()

    async def search(
        self,
        query: str,
        limit: int,
    ) -> tuple[RetrievedItem, ...]:
        if self._client is None or not self.library:
            raise RuntimeError(
                "NeuledgeSystem.search called before index, or library unset",
            )
        text = await self._client.get_docs(library=self.library, topic=query)
        return single_blob_items(
            text,
            source_path=self.library,
            qualified_name=self.library,
        )

    @property
    def gold_resolver(self) -> GoldResolver:
        # WHY: Neuledge returns a single concatenated blob from a
        # non-enumerable local MCP store — no chunk-id store to scan, so
        # ground-truth is fuzzy-matched against the retrieved blob (lazy),
        # same as Context7.
        return LazyFuzzyGoldResolver(DEFAULT_FUZZ_THRESHOLD)

    async def teardown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.__aexit__(None, None, None)
