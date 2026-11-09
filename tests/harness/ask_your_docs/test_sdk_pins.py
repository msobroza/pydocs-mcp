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
import warnings

import httpx
import pytest

openai = pytest.importorskip("openai")
import openai._base_client as sdk_base

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import StripAuthorizationAuth

from ._connection_fakes import RecordingTransport, RotatingBearer, SleepRecorder

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


def test_a_retry_after_above_120_seconds_is_not_retried(monkeypatch) -> None:
    """openai 2.52+ (locked: 2.54.0) honors a server's Retry-After up to 120 s
    (``MAX_RETRY_AFTER_DELAY``) and does not retry above it; 1.x — and 2.x before 2.52 —
    honored only <= 60 s and otherwise backed off and retried. The
    listing and the probe keep ``max_retries=1`` and ``timeout=10``, which does not cover the
    sleep, so this ceiling IS the Connection dialog's worst-case stall on a 429 / 503 — a
    future SDK policy change must fail here, not silently move that stall (Track B D7)."""
    recorder = RecordingTransport([(429, {"retry-after": "121"})])
    sleeps = SleepRecorder()
    monkeypatch.setattr(sdk_base.time, "sleep", sleeps)
    client = openai.OpenAI(
        api_key="sk-test",
        base_url=_URL,
        max_retries=1,
        http_client=openai.DefaultHttpxClient(transport=recorder.transport),
    )
    with pytest.raises(openai.RateLimitError):
        client.models.list()
    assert len(recorder.requests) == 1
    assert sleeps.delays == []


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


def test_model_to_dict_keeps_declared_and_extra_fields() -> None:
    """(e) the listing returns ``entry.to_dict()`` and rung 3 reads BOTH the declared fields and
    the server's own metadata, so the SDK's conversion must keep extras alongside ``id``."""
    entry = openai.types.Model(
        id="my-vlm", created=0, object="model", owned_by="me", capabilities={"vision": True}
    )
    assert entry.to_dict() == {
        "id": "my-vlm",
        "created": 0,
        "object": "model",
        "owned_by": "me",
        "capabilities": {"vision": True},
    }
    # (f) …and it converts an out-of-contract entry SILENTLY under `warnings=False`. An id the
    # endpoint sent as a number makes the serializer warn; the suite runs -W error, so without
    # the kwarg that warning would become the E6 caption in the gate while production read the
    # id — the listing would then behave differently in the two modes.
    numeric = openai.types.Model.construct(id=5, object="model")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert numeric.to_dict(warnings=False) == {"id": 5, "object": "model"}


def test_the_langchain_openai_floor_is_the_pysec_2026_76_fix() -> None:
    """The extra's langchain-openai floor is 1.1.14, the release that fixes PYSEC-2026-76 /
    GHSA-r7w7-9xr2-qq2r (CVE-2026-41488); the <2 cap guards reasoning_capture's private
    overrides. Every release >= 1.1.14 needs openai >= 2.26, so the core pin's floor is 2.26
    (and its <3 cap keeps the 3.x API break out). Both pins are checked in the project metadata
    AND in the lockfile's requirement metadata, so a relock that drifts from them goes red."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extra = project["optional-dependencies"]["harness-ask-your-docs"]
    assert [pin for pin in extra if pin.startswith("langchain-openai")] == [
        "langchain-openai>=1.1.14,<2"
    ]
    core = [pin for pin in project["dependencies"] if pin.startswith("openai")]
    assert core == ["openai>=2.26,<3"]
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert (
        '{ name = "langchain-openai", marker = "extra == \'harness-ask-your-docs\'", '
        'specifier = ">=1.1.14,<2" }' in lock
    )
    assert '{ name = "openai", specifier = ">=2.26,<3" }' in lock


def test_the_installed_chat_openai_exposes_reasoning_effort() -> None:
    pytest.importorskip("langchain_openai")
    from langchain_openai import ChatOpenAI

    assert "reasoning_effort" in ChatOpenAI.model_fields


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
