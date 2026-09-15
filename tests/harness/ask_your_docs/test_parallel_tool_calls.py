"""``ask_your_docs.llm.parallel_tool_calls``: the YAML knob, unset by default.

Spec 2026-07-26 track T4 / user story 21: an endpoint that rejects the field must
keep working, so the flag reaches the chat model ONLY when the YAML sets it. The
kwargs spy is the same seam the AC-19 byte-identity tests use.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("langchain_openai")

import langchain_openai

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    build_chat_model,
    resolve_llm_connection,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import RecordingTransport

_URL = "http://llm.test/v1"


def _connection(block: dict | None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


def _built_kwargs(monkeypatch: pytest.MonkeyPatch, connection, **factory_kwargs) -> dict:
    seen: list[dict] = []

    class _SpyChatOpenAI:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _SpyChatOpenAI)
    build_chat_model(connection, NoBearer(), **factory_kwargs)
    return seen[0]


def test_unset_is_the_default_and_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key in the block ⇒ today's call exactly: no model_kwargs at all."""
    connection = _connection({"base_url": _URL, "model": "m"})
    assert connection.parallel_tool_calls is None
    kwargs = _built_kwargs(monkeypatch, connection, parallel_tool_calls=None)
    assert "model_kwargs" not in kwargs
    assert (kwargs["model"], kwargs["base_url"]) == ("m", _URL)


@pytest.mark.parametrize("flag", [True, False])
def test_a_set_flag_reaches_the_chat_model(monkeypatch: pytest.MonkeyPatch, flag: bool) -> None:
    """Both values are sent — False is a real setting, not an absent one."""
    connection = _connection({"base_url": _URL, "model": "m", "parallel_tool_calls": flag})
    assert connection.parallel_tool_calls is flag
    kwargs = _built_kwargs(monkeypatch, connection, parallel_tool_calls=flag)
    assert kwargs["model_kwargs"] == {"parallel_tool_calls": flag}


def test_the_factory_sends_nothing_unless_the_caller_passes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the main (tool-bound) model passes the flag: the vision model, the
    capability probe and the connection test build from the SAME connection and
    must stay byte-identical, so the factory reads the keyword, not the record."""
    connection = _connection({"base_url": _URL, "model": "m", "parallel_tool_calls": True})
    assert "model_kwargs" not in _built_kwargs(monkeypatch, connection)


def test_the_request_body_carries_the_flag() -> None:
    """End to end on the real SDK: the flag lands in the chat-completions body."""
    connection = _connection({"base_url": _URL, "model": "m", "parallel_tool_calls": False})
    recorder = RecordingTransport([200])
    llm = build_chat_model(
        connection, NoBearer(), transport=recorder.transport, parallel_tool_calls=False
    )
    asyncio.run(llm.ainvoke("hi"))
    body = json.loads(recorder.requests[-1].content)
    assert body["parallel_tool_calls"] is False


def test_the_block_rejects_a_non_boolean() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LlmConnectionConfig(base_url=_URL, model="m", parallel_tool_calls="yes-please")


def test_default_config_documents_the_knob_without_enabling_it() -> None:
    """A commented example only — the shipped default must stay unset."""
    import importlib.resources
    from pathlib import Path

    import yaml

    path = importlib.resources.files("pydocs_mcp.defaults").joinpath("default_config.yaml")
    text = Path(str(path)).read_text(encoding="utf-8")
    assert yaml.safe_load(text)["ask_your_docs"]["llm"] is None
    assert "#   parallel_tool_calls: null" in text
