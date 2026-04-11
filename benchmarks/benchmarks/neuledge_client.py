"""Neuledge Context MCP client over Streamable HTTP.

Neuledge Context is a local-first documentation MCP server backed by SQLite FTS5.
It exposes a `get_docs` tool with `library` and `topic` parameters.

The server must be running locally:
    npm install -g @neuledge/context
    context install requests pandas numpy
    context serve --http 8080

This client connects via MCP Streamable HTTP at http://localhost:8080/mcp.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


class NeuledgeError(Exception):
    """Raised when a Neuledge Context MCP call fails."""


@dataclass
class NeuledgeClient:
    """Async MCP client for Neuledge Context HTTP server."""

    base_url: str = "http://localhost:8080/mcp"
    _timeout: float = 30.0
    _http: httpx.AsyncClient | None = None
    _request_id: int = 0

    async def __aenter__(self) -> "NeuledgeClient":
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": "application/json, text/event-stream"},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool and return the text content."""
        assert self._http is not None
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        resp = await self._http.post(self.base_url, json=payload)
        resp.raise_for_status()

        data = resp.json()
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
            library: Package identifier (e.g. "pandas", "numpy").
            topic: Search query / topic string.

        Returns:
            Documentation text from Neuledge Context.
        """
        return await self._call_tool("get_docs", {
            "library": library,
            "topic": topic,
        })
