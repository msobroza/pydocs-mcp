"""NeuledgeClient + NeuledgeSystem tests — mocked, no real network.

Mirrors ``test_context7_system.py``'s failure-atomicity coverage and
extends it to pin every NeuledgeClient code path: SSE parsing, the MCP
initialize handshake, tool-call success / error / HTTP-error branches,
and the NeuledgeSystem index → search → teardown lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydocs_eval.registries import system_registry
from pydocs_eval.systems import NeuledgeSystem
from pydocs_eval.systems.neuledge import (
    NeuledgeClient,
    NeuledgeError,
    _parse_sse_json,
)
from pydocs_mcp.retrieval.config import AppConfig

# ── _parse_sse_json ──────────────────────────────────────────────────────────


def test_parse_sse_json_extracts_data_line():
    body = 'event: message\ndata: {"result": {"ok": true}, "jsonrpc": "2.0", "id": 1}\n'
    assert _parse_sse_json(body) == {
        "result": {"ok": True},
        "jsonrpc": "2.0",
        "id": 1,
    }


def test_parse_sse_json_falls_back_to_plain_json():
    # WHY: pin the fallback so a non-SSE server still works.
    assert _parse_sse_json('{"result": 42}') == {"result": 42}


# ── NeuledgeClient ───────────────────────────────────────────────────────────


class _StubResponse:
    """Mimics httpx.Response — just the bits NeuledgeClient touches."""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        session_id: str | None = "stub-session",
        raise_status: Exception | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = {"mcp-session-id": session_id} if session_id else {}
        self._raise_status = raise_status

    @property
    def text(self) -> str:
        return "event: message\ndata: " + json.dumps(self._payload) + "\n"

    def raise_for_status(self) -> None:
        if self._raise_status is not None:
            raise self._raise_status


class _StubHttpClient:
    """Mimics httpx.AsyncClient.

    ``responses`` is consumed in order on each ``post``. ``raise_on_post``
    short-circuits with the given exception instead of returning a response.
    """

    def __init__(
        self,
        responses: list[_StubResponse] | None = None,
        raise_on_post: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.raise_on_post = raise_on_post
        self.posts: list[tuple[str, dict, dict]] = []
        self.closed = False

    async def post(self, url: str, *, json: dict, headers: dict | None = None):
        self.posts.append((url, json, headers or {}))
        if self.raise_on_post is not None:
            raise self.raise_on_post
        if not self.responses:
            return _StubResponse({"result": {}})
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _client_with_stub(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[_StubResponse] | None = None,
    *,
    raise_on_post: Exception | None = None,
) -> tuple[NeuledgeClient, _StubHttpClient]:
    """Patch the httpx.AsyncClient constructor so NeuledgeClient uses a stub."""
    stub = _StubHttpClient(responses=responses, raise_on_post=raise_on_post)
    monkeypatch.setattr(
        "pydocs_eval.systems.neuledge.httpx.AsyncClient",
        lambda *args, **kwargs: stub,
    )
    return NeuledgeClient(base_url="http://stub/mcp"), stub


@pytest.mark.asyncio
async def test_client_aenter_runs_initialize_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, stub = _client_with_stub(
        monkeypatch,
        responses=[
            _StubResponse({"result": {"protocolVersion": "2024-11-05"}}),
            # Second post is the ``notifications/initialized`` notification.
            _StubResponse({}),
        ],
    )
    async with client:
        # Two posts: initialize + initialized notification.
        assert len(stub.posts) == 2
        assert stub.posts[0][1]["method"] == "initialize"
        assert stub.posts[1][1]["method"] == "notifications/initialized"
    assert stub.closed is True


@pytest.mark.asyncio
async def test_initialize_raises_neuledge_error_on_mcp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_with_stub(
        monkeypatch,
        responses=[_StubResponse({"error": {"code": -1, "message": "nope"}})],
    )
    with pytest.raises(NeuledgeError, match="MCP initialize error"):
        async with client:
            pass


@pytest.mark.asyncio
async def test_get_docs_returns_concatenated_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_with_stub(
        monkeypatch,
        responses=[
            _StubResponse({"result": {}}),  # initialize
            _StubResponse({}),  # initialized notification
            _StubResponse(
                {
                    "result": {
                        "content": [
                            {"type": "text", "text": "para 1"},
                            {"type": "text", "text": "para 2"},
                            {"type": "image", "data": "base64"},  # filtered
                        ]
                    }
                }
            ),
        ],
    )
    async with client:
        docs = await client.get_docs("pandas@2.0.0", "DataFrame merge")
    assert docs == "para 1\npara 2"


@pytest.mark.asyncio
async def test_call_tool_raises_on_tool_error_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_with_stub(
        monkeypatch,
        responses=[
            _StubResponse({"result": {}}),
            _StubResponse({}),
            _StubResponse(
                {
                    "result": {
                        "isError": True,
                        "content": [{"text": "library not found"}],
                    }
                }
            ),
        ],
    )
    async with client:
        with pytest.raises(NeuledgeError, match="Tool error: library not found"):
            await client.get_docs("missing", "topic")


@pytest.mark.asyncio
async def test_call_tool_raises_on_mcp_jsonrpc_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client_with_stub(
        monkeypatch,
        responses=[
            _StubResponse({"result": {}}),
            _StubResponse({}),
            _StubResponse({"error": {"code": -32601, "message": "method not found"}}),
        ],
    )
    async with client:
        with pytest.raises(NeuledgeError, match="MCP error"):
            await client.get_docs("any", "topic")


@pytest.mark.asyncio
async def test_call_tool_wraps_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = _StubResponse(
        {"result": {}},
        status_code=500,
        raise_status=httpx.HTTPStatusError(
            "500",
            request=httpx.Request("POST", "http://stub"),
            response=httpx.Response(500),
        ),
    )
    client, _ = _client_with_stub(
        monkeypatch,
        responses=[_StubResponse({"result": {}}), _StubResponse({}), failing],
    )
    async with client:
        with pytest.raises(NeuledgeError, match="HTTP 500"):
            await client.get_docs("any", "topic")


@pytest.mark.asyncio
async def test_call_tool_wraps_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, stub = _client_with_stub(
        monkeypatch,
        responses=[_StubResponse({"result": {}}), _StubResponse({})],
    )
    async with client:
        stub.raise_on_post = httpx.ConnectError("connection refused")
        with pytest.raises(NeuledgeError, match="Network error"):
            await client.get_docs("any", "topic")


# ── NeuledgeSystem ───────────────────────────────────────────────────────────


class _StubNeuledgeClient:
    """Async-context-manager stub for NeuledgeSystem.

    Records lifecycle events and returns canned ``get_docs`` payloads so
    tests pin the System adapter without speaking MCP.
    """

    def __init__(self, *, base_url: str = "http://stub", docs: str = "the docs") -> None:
        self.base_url = base_url
        self._docs = docs
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _StubNeuledgeClient:
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def get_docs(self, library: str, topic: str) -> str:
        return self._docs


@pytest.mark.asyncio
async def test_system_index_opens_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _StubNeuledgeClient()
    import pydocs_eval.systems.neuledge as nl

    monkeypatch.setattr(nl, "NeuledgeClient", lambda *a, **kw: stub)
    system = system_registry.build("neuledge")
    await system.index(tmp_path, AppConfig.load())
    assert stub.entered is True
    assert system._client is stub


@pytest.mark.asyncio
async def test_system_search_returns_one_item_with_library_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _StubNeuledgeClient(docs="merging dataframes\n…")
    import pydocs_eval.systems.neuledge as nl

    monkeypatch.setattr(nl, "NeuledgeClient", lambda *a, **kw: stub)
    system = system_registry.build("neuledge")
    system.library = "pandas@2.0.0"
    await system.index(tmp_path, AppConfig.load())
    results = await system.search("DataFrame merge", limit=5)
    assert len(results) == 1
    assert results[0].rank == 1
    assert results[0].text == "merging dataframes\n…"
    assert results[0].source_path == "pandas@2.0.0"
    assert results[0].qualified_name == "pandas@2.0.0"


@pytest.mark.asyncio
async def test_system_search_returns_empty_tuple_on_empty_docs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _StubNeuledgeClient(docs="")
    import pydocs_eval.systems.neuledge as nl

    monkeypatch.setattr(nl, "NeuledgeClient", lambda *a, **kw: stub)
    system = system_registry.build("neuledge")
    system.library = "fastapi@0.115.0"
    await system.index(tmp_path, AppConfig.load())
    assert await system.search("query", limit=1) == ()


@pytest.mark.asyncio
async def test_system_search_raises_when_library_unset(tmp_path: Path) -> None:
    # WHY: the runner is responsible for seeding ``library`` per task; a
    # missing value should fail loudly, not silently return nothing.
    system = system_registry.build("neuledge")
    # Skip the actual index() to avoid touching the network — directly set
    # _client so the unset-library branch is the only one that can fire.
    system._client = _StubNeuledgeClient()
    with pytest.raises(RuntimeError, match="library unset"):
        await system.search("anything", limit=1)


@pytest.mark.asyncio
async def test_system_search_raises_when_index_not_called(tmp_path: Path) -> None:
    system = system_registry.build("neuledge")
    system.library = "pandas@2.0.0"
    with pytest.raises(RuntimeError, match="before index"):
        await system.search("anything", limit=1)


@pytest.mark.asyncio
async def test_system_teardown_closes_client_and_clears_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _StubNeuledgeClient()
    import pydocs_eval.systems.neuledge as nl

    monkeypatch.setattr(nl, "NeuledgeClient", lambda *a, **kw: stub)
    system = system_registry.build("neuledge")
    await system.index(tmp_path, AppConfig.load())
    await system.teardown()
    assert stub.exited is True
    assert system._client is None


@pytest.mark.asyncio
async def test_system_teardown_is_idempotent() -> None:
    # WHY: teardown without a prior index() must not raise — same contract
    # as PydocsMcpSystem.teardown so the runner's failure-path can always
    # call it.
    system = system_registry.build("neuledge")
    await system.teardown()
    await system.teardown()
    assert system._client is None
