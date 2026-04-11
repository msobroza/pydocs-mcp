"""Async HTTP client for Neuledge Context MCP endpoint.

Neuledge Context is a local-first documentation MCP server backed by SQLite FTS5.
It exposes a `get_docs` tool with `library` and `topic` parameters.

The server must be running locally:
    context serve --http 8080

This client connects via MCP Streamable HTTP at http://localhost:8080/mcp.
The server uses Server-Sent Events (SSE) format for responses and requires
an MCP initialize handshake before tool calls.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx


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
