"""AC-38: the SDK behaviors the renew-on-401 design leans on, pinned on the installed
(locked) toolkit so a future bump goes red instead of silently disabling renewal.

(a) a callable api_key is re-evaluated before every attempt (sync AND async clients — the
async client awaits its provider); (b) a placeholder key + StripAuthorizationAuth leaves no
header on the wire; (c) an httpx client-level auth is honored by the SDK's send; (d)
langchain-openai runs a SYNC api_key callable on an executor thread for the async client —
the factory hands the SDK ``bearer.current``, whose first call may block on a bounded token
fetch, and that must never run on the event-loop thread.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

openai = pytest.importorskip("openai")
import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import StripAuthorizationAuth

from ._connection_fakes import RecordingTransport, RotatingBearer

_URL = "http://llm.test/v1"


@pytest.fixture(autouse=True)
def _fast_sdk_retries(monkeypatch):
    monkeypatch.setattr(sdk_base, "INITIAL_RETRY_DELAY", 0.0)
    monkeypatch.setattr(sdk_base, "MAX_RETRY_DELAY", 0.0)


def test_sync_client_reevaluates_a_callable_api_key_per_attempt() -> None:
    recorder = RecordingTransport([500, 200])
    bearer = RotatingBearer()
    client = openai.OpenAI(
        api_key=bearer.current,
        base_url=_URL,
        max_retries=1,
        http_client=openai.DefaultHttpxClient(transport=recorder.transport),
    )
    ids = [m.id for m in client.models.list().data]
    assert ids == ["model-a", "model-b"]
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2"]


def test_async_client_awaits_its_api_key_provider() -> None:
    recorder = RecordingTransport([500, 200])
    bearer = RotatingBearer()

    async def provider() -> str:
        return bearer.current()

    async def go() -> list[str]:
        client = openai.AsyncOpenAI(
            api_key=provider,
            base_url=_URL,
            max_retries=1,
            http_client=openai.DefaultAsyncHttpxClient(transport=recorder.transport),
        )
        return [m.id for m in (await client.models.list()).data]

    assert asyncio.run(go()) == ["model-a", "model-b"]
    assert recorder.authorizations() == ["Bearer k1", "Bearer k2"]


def test_placeholder_key_with_strip_auth_sends_no_header() -> None:
    recorder = RecordingTransport([200])
    client = openai.OpenAI(
        api_key="no-auth",
        base_url=_URL,
        http_client=openai.DefaultHttpxClient(
            auth=StripAuthorizationAuth(), transport=recorder.transport
        ),
    )
    client.models.list()
    assert recorder.authorizations() == [None]


def test_client_level_httpx_auth_is_honored_by_send() -> None:
    class _CountingAuth(httpx.Auth):
        def __init__(self) -> None:
            self.flows = 0

        def auth_flow(self, request: httpx.Request):
            self.flows += 1
            yield request

    recorder = RecordingTransport([200])
    counting = _CountingAuth()
    client = openai.OpenAI(
        api_key="k",
        base_url=_URL,
        http_client=openai.DefaultHttpxClient(auth=counting, transport=recorder.transport),
    )
    client.models.list()
    assert counting.flows == 1
    # The SDK passes no auth of its own, so the client-level one is what applies.
    assert client.custom_auth is None


def test_a_sync_api_key_callable_runs_off_the_event_loop_thread() -> None:
    """The blocking first token fetch rides the executor, not the loop (langchain-openai
    wraps a sync callable in run_in_executor for the async client)."""
    pytest.importorskip("langchain_openai")
    from langchain_openai import ChatOpenAI

    recorder = RecordingTransport([200])
    called_on: list[int] = []

    def api_key() -> str:
        called_on.append(threading.get_ident())
        return "k"

    async def go() -> int:
        llm = ChatOpenAI(
            model="m",
            base_url=_URL,
            api_key=api_key,
            http_client=openai.DefaultHttpxClient(transport=recorder.transport),
            http_async_client=openai.DefaultAsyncHttpxClient(transport=recorder.transport),
        )
        await llm.ainvoke("hi")
        return threading.get_ident()

    loop_thread = asyncio.run(go())
    assert called_on and all(thread != loop_thread for thread in called_on)
