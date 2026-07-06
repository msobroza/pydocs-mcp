"""Shared MCP-over-HTTP base: divergence hooks + strengthened error ladder
+ the shared single-blob RetrievedItem emission."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from benchmarks.eval.systems._mcp_http import McpHttpClient
from benchmarks.eval.systems.base_system import single_blob_items
from benchmarks.eval.systems.context7 import Context7Client, Context7Error
from benchmarks.eval.systems.neuledge import NeuledgeClient


def test_both_clients_share_the_base() -> None:
    assert issubclass(Context7Client, McpHttpClient)
    assert issubclass(NeuledgeClient, McpHttpClient)


@pytest.mark.asyncio
async def test_context7_raises_on_top_level_jsonrpc_error() -> None:
    # Deliberate strengthening: Context7 previously ignored the top-level
    # JSON-RPC "error" member; the shared ladder maps it to the domain
    # error, as Neuledge always did.
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"error": {"code": -32601, "message": "method not found"}}
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        async with Context7Client() as client:
            with pytest.raises(Context7Error, match="MCP error"):
                await client.query_docs("/psf/requests", "GET request")


@pytest.mark.asyncio
async def test_context7_request_ids_increment() -> None:
    # Deliberate change: Context7 previously hardcoded ``id: 1``; the
    # shared base issues monotonically increasing ids like Neuledge.
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"result": {"content": [{"text": "docs"}]}})
    post = AsyncMock(return_value=mock_response)
    with patch("httpx.AsyncClient.post", new=post):
        async with Context7Client() as client:
            await client.query_docs("/psf/requests", "one")
            await client.query_docs("/psf/requests", "two")
    ids = [call.kwargs["json"]["id"] for call in post.call_args_list]
    assert ids == [1, 2]


def test_single_blob_items_empty_text_is_no_items() -> None:
    assert single_blob_items("", source_path="s", qualified_name=None) == ()


def test_single_blob_items_rank1_item() -> None:
    (item,) = single_blob_items("docs", source_path="/psf/requests", qualified_name="requests")
    assert item.rank == 1
    assert item.text == "docs"
    assert item.source_path == "/psf/requests"
    assert item.qualified_name == "requests"
