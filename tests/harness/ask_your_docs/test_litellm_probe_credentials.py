"""The LiteLLM probe's one raw request sends the bearer and nothing else (Track B D6, T10).

openai 2 reads ``OPENAI_ADMIN_KEY`` into every client, and a raw ``client.get`` keeps
the SDK's default security — bearer OR admin key — so with an empty bearer at request
time it sends ``Authorization: Bearer <admin key>`` to the endpoint being probed.
Resource calls narrow themselves to bearer-only; the probe's ``/model_group/info`` get
must too. Core dependencies only at module level (the SDK, httpx, the harness's
core-importable modules), so this runs in GitHub CI's dev-group-only install.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

openai_models = pytest.importorskip("openai._models")

from pydocs_mcp.harness.ask_your_docs import litellm_probe
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    resolve_llm_connection,
)

from ._connection_fakes import EmptyAtRequestBearer, FakeBearer, RecordingTransport

_URL = "http://llm.test/v1"
_ADMIN_KEY = "sk-admin-SYNTHETIC-t10"
_PROBE_LOGGER = "pydocs-mcp.harness.ask-your-docs"
# openai 2.34+ takes a per-request ``security`` option: the bearer-only probe then refuses an
# empty bearer locally, before any request. Older releases have no option and send the GET.
_SDK_HAS_PER_REQUEST_SECURITY = "security" in openai_models.FinalRequestOptions.model_fields


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    litellm_probe.clear_litellm_probe_cache()
    yield
    litellm_probe.clear_litellm_probe_cache()


def _no_block_connection():
    """No ``ask_your_docs.llm`` block: the listing's eager non-empty bearer check applies."""
    environment = {"OPENAI_BASE_URL": _URL, "LLM_MODEL": "m"}
    return resolve_llm_connection(
        None, environment, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


class GroupInfoFailureRecorder:
    """The probe's own fetch behind its ``group_info`` seam, recording what it raises.

    WHY: the probe fails soft, and its ``litellm_probe_failed`` line carries the status
    alone, so ``None`` alone cannot say why it failed. This keeps the real request path
    (``_group_info_payload`` over the recording transport) and notes the raised class.
    """

    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.raised: list[type[BaseException]] = []

    async def __call__(self, connection: Any, bearer: Any) -> Any:
        try:
            return await litellm_probe._group_info_payload(connection, bearer, self.transport)
        except Exception as exc:
            self.raised.append(type(exc))
            raise


def _probe_failures(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == _PROBE_LOGGER]
    return [record for record in records if record.get("event") == "litellm_probe_failed"]


def _header_values(recorder: RecordingTransport) -> list[str]:
    """Every header value the transport saw, including the per-call Authorization snapshots."""
    values = [value for request in recorder.requests for value in request.headers.values()]
    return values + [auth for auth in recorder.authorizations() if auth is not None]


def test_the_probe_never_sends_an_admin_key_as_its_bearer(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENAI_ADMIN_KEY", _ADMIN_KEY)
    connection = _no_block_connection()
    assert connection.block_present is False  # the eager-check path, not a configured key
    bearer = EmptyAtRequestBearer()
    recorder = RecordingTransport()
    fetch = GroupInfoFailureRecorder(recorder.transport)

    with caplog.at_level(logging.INFO, logger=_PROBE_LOGGER):
        rows = asyncio.run(
            litellm_probe.fetch_litellm_group_info(connection, bearer, group_info=fetch)
        )

    assert rows is None  # fails soft: the probe only refines what the dialog shows
    assert bearer.calls >= 2  # the eager check passed AND the request-time read came back empty
    assert not any(_ADMIN_KEY in value for value in _header_values(recorder))
    assert [record["status"] for record in _probe_failures(caplog)] == [None]
    if _SDK_HAS_PER_REQUEST_SECURITY:
        # Bearer-only security with an empty bearer: the SDK refuses before any request.
        assert recorder.requests == []
        assert fetch.raised == [TypeError]
    else:
        # No per-request security: the GET goes out once, with no Authorization header.
        assert len(recorder.requests) == 1
        assert recorder.authorizations() == [None]


def test_the_bearer_only_probe_still_sends_a_real_bearer(monkeypatch) -> None:
    """The narrowing keeps the ordinary path: the request goes out with the bearer alone."""
    monkeypatch.setenv("OPENAI_ADMIN_KEY", _ADMIN_KEY)
    recorder = RecordingTransport()

    asyncio.run(
        litellm_probe.fetch_litellm_group_info(
            _no_block_connection(), FakeBearer("tok-probe-abcd"), transport=recorder.transport
        )
    )

    (request,) = recorder.requests
    assert request.url.path == "/model_group/info"
    assert recorder.authorizations() == ["Bearer tok-probe-abcd"]
    assert not any(_ADMIN_KEY in value for value in _header_values(recorder))
