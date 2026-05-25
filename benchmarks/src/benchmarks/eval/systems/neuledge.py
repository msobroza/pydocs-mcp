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
from typing import TYPE_CHECKING, Optional

import httpx

from ..gold_resolver import _DEFAULT_FUZZ_THRESHOLD, LazyFuzzyGoldResolver
from ..serialization import system_registry
from .base_system import RetrievedItem

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
            payload = line[len("data:"):].strip()
            return json.loads(payload)
    # Fallback: try parsing the whole body as JSON
    return json.loads(text)


class NeuledgeClient:
    """Async context-manager client for Neuledge Context MCP tools.

    Usage::

        async with NeuledgeClient() as client:
            docs = await client.get_docs("pandas", "DataFrame merge")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/mcp",
        timeout: float = 30.0,
    ):
        self._base_url = base_url
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None
        self._request_id: int = 0
        self._session_id: Optional[str] = None

    async def __aenter__(self) -> "NeuledgeClient":
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": "application/json, text/event-stream"},
        )
        await self._initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

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

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """POST a JSON-RPC tool call and return the first text content block."""
        assert self._http is not None
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            resp = await self._http.post(self._base_url, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise NeuledgeError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise NeuledgeError(f"Network error: {exc}") from exc

        data = _parse_sse_json(resp.text)

        if "error" in data:
            raise NeuledgeError(f"MCP error: {data['error']}")

        result = data.get("result", {})
        if result.get("isError"):
            content = result.get("content", [{}])
            msg = content[0].get("text", str(content)) if content else "unknown error"
            raise NeuledgeError(f"Tool error: {msg}")

        content = result.get("content", [])
        texts = [c["text"] for c in content if c.get("type") == "text"]
        return "\n".join(texts)

    async def get_docs(self, library: str, topic: str) -> str:
        """Search documentation for a library by topic.

        Args:
            library: Package identifier as shown in `context list`
                     (e.g. "pandas@2.2.2", "numpy@2.0.0").
            topic: Search query / topic string.

        Returns:
            Documentation text from Neuledge Context.
        """
        return await self._call_tool("get_docs", {
            "library": library,
            "topic": topic,
        })


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
    _client: "NeuledgeClient | None" = field(
        default=None, init=False, repr=False,
    )

    async def index(self, corpus_dir: Path, config: "AppConfig") -> None:  # noqa: ARG002
        if self._client is None:
            self._client = NeuledgeClient(base_url=self.base_url)
            await self._client.__aenter__()

    async def search(
        self, query: str, limit: int,  # noqa: ARG002 -- Neuledge returns one blob
    ) -> tuple[RetrievedItem, ...]:
        if self._client is None or not self.library:
            raise RuntimeError(
                "NeuledgeSystem.search called before index, or library unset",
            )
        text = await self._client.get_docs(library=self.library, topic=query)
        if not text:
            return ()
        return (
            RetrievedItem(
                rank=1,
                text=text,
                source_path=self.library,
                qualified_name=self.library,
            ),
        )

    @property
    def gold_resolver(self) -> "GoldResolver":
        # WHY: Neuledge returns a single concatenated blob from a
        # non-enumerable local MCP store — no chunk-id store to scan, so
        # ground-truth is fuzzy-matched against the retrieved blob (lazy),
        # same as Context7.
        return LazyFuzzyGoldResolver(_DEFAULT_FUZZ_THRESHOLD)

    async def teardown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.__aexit__(None, None, None)
