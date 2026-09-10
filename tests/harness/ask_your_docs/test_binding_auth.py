"""harness/ask_your_docs/binding — the agent run's auth-failure boundary (E4/H4).

A sibling module rather than more of ``test_binding.py``, which is already past the
readable-in-one-read budget. Scope: what the binding raises when the ENDPOINT rejects
the credential mid-run — the page seals that boundary, and the eval path must too,
because a campaign log is read by people and stored for the life of the experiment.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerRejectedError
from pydocs_mcp.harness.ask_your_docs.llm_connection import clear_bearer_registry

from tests.harness.core._runner_contract import conformant_sample

_TOKEN = "tok-campaign-4242"


def _settings(tmp_path: Path) -> dict[str, object]:
    return {
        "workspace": str(tmp_path / "ws"),
        "model": "fake-model",
        "base_url": "http://llm.internal/v1",
        "trace_root": str(tmp_path / "traces"),
    }


class _RejectingGraph:
    """A graph whose model call is rejected by a gateway that echoes the header back.

    The body is the shape a real one sends: the presented credential quoted inside the
    endpoint's own prose, which the SDK carries verbatim in ``str(exc)``.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _state, _config):
        import httpx
        import openai

        self.calls += 1
        request = httpx.Request("POST", "http://llm.internal/v1/chat/completions")
        body = {"error": {"message": f"rejected Bearer {_TOKEN} for tenant acme"}}
        response = httpx.Response(401, json=body, request=request)
        # The message the SDK itself builds (openai._client._make_status_error): the
        # status and the WHOLE body, which is why an untranslated error is a leak.
        raise openai.AuthenticationError(f"Error code: 401 - {body}", response=response, body=body)


@contextlib.asynccontextmanager
async def _no_serve_session(_settings, _trace_env):
    """The run's serve session, minus the subprocess: the LLM call is what is under test."""
    yield []


async def _execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, graph) -> None:
    """One ``_build_and_execute`` over ``graph``, with the server and the agent build faked."""

    async def _fake_build_agent(*_args, **_kwargs):
        return graph, object()

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _no_serve_session)
    await binding._build_and_execute(
        sample=conformant_sample(),
        settings=binding.AskYourDocsRunnerSettings.model_validate(_settings(tmp_path)),
        overrides=binding.PromptOverrides(),
        skill_override=None,
        task_name=None,
        trace_env={},
    )


async def test_a_rejected_credential_leaves_the_run_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4/H4 at the eval path: the SDK's 401 becomes ``BearerRejectedError``, last four only.

    Unwrapped, ``openai.AuthenticationError`` escapes ``run_task`` into whatever a campaign
    runner logs — carrying the response body, and with it the credential the gateway echoed.
    """
    monkeypatch.setenv("OPENAI_API_KEY", _TOKEN)
    clear_bearer_registry()
    graph = _RejectingGraph()
    with pytest.raises(BearerRejectedError) as excinfo:
        await _execute(tmp_path, monkeypatch, graph)
    clear_bearer_registry()
    message = str(excinfo.value)
    assert graph.calls == 1
    assert "…4242" in message and "llm.internal" in message
    for leak in (_TOKEN, "tenant acme", "Bearer "):
        assert leak not in message
