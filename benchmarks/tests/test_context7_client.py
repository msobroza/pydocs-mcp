"""Tests for Context7 client — mocked, no real network calls."""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from benchmarks.context7_client import Context7Client, Context7Error


@pytest.mark.asyncio
async def test_resolve_library_id_returns_id():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "result": {"content": [{"text": "/requests/requests"}]}
    })

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        async with Context7Client() as client:
            lib_id = await client.resolve_library_id("requests")
    assert lib_id == "/requests/requests"


@pytest.mark.asyncio
async def test_get_library_docs_returns_text():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "result": {"content": [{"text": "requests docs content here"}]}
    })

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        async with Context7Client() as client:
            docs = await client.get_library_docs("/requests/requests", query="GET request")
    assert "requests" in docs


@pytest.mark.asyncio
async def test_raises_context7_error_on_http_error():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock()
    ))
    mock_response.json = MagicMock(return_value={})

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(Context7Error):
            async with Context7Client() as client:
                await client.resolve_library_id("nonexistent-lib")
