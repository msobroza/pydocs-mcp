"""Shared MCP-over-Streamable-HTTP client base for remote-system adapters.

Context7 and Neuledge (and any future hosted-docs comparator) speak the
same JSON-RPC ``tools/call`` POST protocol over httpx and share the same
three-stage error ladder (HTTP status → transport → tool ``isError``).
This base owns the httpx lifecycle, request ids, payload construction and
the error ladder; the four REAL divergence points between the servers are
subclass hooks:

- ``error_cls`` + the ``*_format`` class attributes — each adapter's
  domain exception and its pinned message spellings (adapter test suites
  match these strings; the extraction must not rewrite them).
- ``_decode`` — plain JSON (Context7) vs SSE-framed JSON (Neuledge).
- ``_headers`` — extra per-request headers (Neuledge's ``Mcp-Session-Id``).
- ``_extract_text`` — strict first-block (Context7) vs
  join-all-text-blocks (Neuledge).
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

# Single source of truth for the shared transport defaults — previously
# duplicated across context7.py / neuledge.py.
_DEFAULT_TIMEOUT = 30.0
_ACCEPT_HEADER = "application/json, text/event-stream"


class McpHttpClient:
    """Async context-manager base for MCP Streamable-HTTP tool calls."""

    error_cls: ClassVar[type[Exception]] = Exception
    http_error_format: ClassVar[str] = "HTTP {status}"
    network_error_format: ClassVar[str] = "Network error: {exc}"
    tool_error_format: ClassVar[str] = "Tool error: {msg}"
    rpc_error_format: ClassVar[str] = "MCP error: {error}"

    def __init__(self, base_url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._request_id = 0

    async def __aenter__(self) -> McpHttpClient:
        self._http = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": _ACCEPT_HEADER},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    # ── divergence hooks ─────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        """Extra per-request headers (e.g. an MCP session id)."""
        return {}

    def _decode(self, resp: httpx.Response) -> dict[str, Any]:
        """Response body → JSON-RPC envelope dict."""
        return resp.json()

    def _extract_text(self, result: dict[str, Any]) -> str:
        """Successful ``result`` → text payload. Server-shape specific."""
        raise NotImplementedError

    # ── shared protocol ──────────────────────────────────────────────

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """POST a JSON-RPC ``tools/call`` and return the tool's text."""
        assert self._http is not None, "use 'async with' before call_tool"
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = await self._http.post(self._base_url, json=payload, headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self.error_cls(
                self.http_error_format.format(status=exc.response.status_code)
            ) from exc
        except httpx.RequestError as exc:
            raise self.error_cls(self.network_error_format.format(exc=exc)) from exc

        data = self._decode(resp)

        # Top-level JSON-RPC error. Previously only Neuledge checked this —
        # covering Context7 too is a deliberate strengthening of its ladder.
        if "error" in data:
            raise self.error_cls(self.rpc_error_format.format(error=data["error"]))

        result = data.get("result", {})
        if result.get("isError"):
            content = result.get("content", [{}])
            msg = content[0].get("text", "unknown error") if content else "unknown error"
            raise self.error_cls(self.tool_error_format.format(msg=msg))

        return self._extract_text(result)


__all__ = ("McpHttpClient",)
