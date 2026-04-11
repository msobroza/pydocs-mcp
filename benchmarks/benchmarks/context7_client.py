"""Async HTTP client for Context7 MCP endpoint.

Context7 exposes an MCP server at https://mcp.context7.com/mcp with two tools:
  - resolve-library-id(libraryName, query) → returns a canonical library ID string
  - query-docs(libraryId, query) → returns doc text

We communicate via MCP Streamable HTTP transport (JSON-RPC POST with Accept header).
"""
from __future__ import annotations

from typing import Optional

import httpx

CONTEXT7_BASE_URL = "https://mcp.context7.com/mcp"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_TOKENS = 5000


class Context7Error(Exception):
    """Raised when Context7 returns an error or unexpected response."""


class Context7Client:
    """Async context-manager client for Context7 MCP tools.

    Usage::

        async with Context7Client() as client:
            lib_id = await client.resolve_library_id("requests")
            docs = await client.get_library_docs(lib_id, query="GET request")
    """

    def __init__(self, base_url: str = CONTEXT7_BASE_URL, timeout: float = _DEFAULT_TIMEOUT):
        self._base_url = base_url
        self._timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "Context7Client":
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": "application/json, text/event-stream"},
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()

    async def _call_tool(self, tool_name: str, arguments: dict) -> str:
        """POST a JSON-RPC tool call and return the first text content block."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = await self._http.post(self._base_url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise Context7Error(f"HTTP {exc.response.status_code} from Context7") from exc
        except httpx.RequestError as exc:
            raise Context7Error(f"Network error contacting Context7: {exc}") from exc

        data = resp.json()

        # Check for MCP-level errors in the response content
        result = data.get("result", {})
        if result.get("isError"):
            content = result.get("content", [{}])
            msg = content[0].get("text", "Unknown error") if content else "Unknown error"
            raise Context7Error(f"Context7 tool error: {msg}")

        try:
            content = result["content"]
            return content[0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise Context7Error(f"Unexpected Context7 response shape: {data!r}") from exc

    async def resolve_library_id(self, library_name: str, query: str = "") -> str:
        """Call resolve-library-id and return the canonical library ID.

        Args:
            library_name: Human name like 'requests' or 'pandas'.
            query: Context about what the user is trying to do.

        Returns:
            Canonical ID string like '/psf/requests'.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        text = await self._call_tool("resolve-library-id", {
            "libraryName": library_name,
            "query": query or f"How to use {library_name}",
        })
        # Response format: "- Context7-compatible library ID: /org/project"
        for line in text.splitlines():
            stripped = line.strip()
            if "Context7-compatible library ID:" in stripped:
                lib_id = stripped.split("Context7-compatible library ID:")[-1].strip()
                if lib_id.startswith("/"):
                    return lib_id
            # Fallback: line starting with /org/project pattern
            elif stripped.startswith("/") and "/" in stripped[1:]:
                return stripped.split()[0]
        raise Context7Error(f"Could not parse library ID from response: {text[:300]}")

    async def query_docs(
        self,
        library_id: str,
        query: str,
    ) -> str:
        """Call query-docs and return documentation text.

        Args:
            library_id: Canonical ID from resolve_library_id.
            query: Search query to focus the returned docs.

        Returns:
            Documentation text string.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        return await self._call_tool("query-docs", {
            "libraryId": library_id,
            "query": query,
        })

    async def get_library_docs(
        self,
        library_id: str,
        query: str,
        topic: str = "",
        tokens: int = _DEFAULT_TOKENS,
    ) -> str:
        """Alias for query_docs (backward compatibility)."""
        return await self.query_docs(library_id, query)
