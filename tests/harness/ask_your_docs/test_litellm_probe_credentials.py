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

import pytest

pytest.importorskip("openai")

from pydocs_mcp.harness.ask_your_docs import litellm_probe
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    resolve_llm_connection,
)

from ._connection_fakes import EmptyAtRequestBearer, RecordingTransport

_URL = "http://llm.test/v1"
_ADMIN_KEY = "sk-admin-SYNTHETIC-t10"


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


def _header_values(recorder: RecordingTransport) -> list[str]:
    """Every header value the transport saw, including the per-call Authorization snapshots."""
    values = [value for request in recorder.requests for value in request.headers.values()]
    return values + [auth for auth in recorder.authorizations() if auth is not None]


def test_the_probe_never_sends_an_admin_key_as_its_bearer(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_ADMIN_KEY", _ADMIN_KEY)
    connection = _no_block_connection()
    assert connection.block_present is False  # the eager-check path, not a configured key
    bearer = EmptyAtRequestBearer()
    recorder = RecordingTransport()

    rows = asyncio.run(
        litellm_probe.fetch_litellm_group_info(connection, bearer, transport=recorder.transport)
    )

    assert rows is None  # fails soft: the probe only refines what the dialog shows
    assert bearer.calls >= 2  # the eager check passed AND the request-time read came back empty
    assert not any(_ADMIN_KEY in value for value in _header_values(recorder))
