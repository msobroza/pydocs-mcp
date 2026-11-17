"""Lock pins on what the chat factory's model sends and records (Track B D11, T21 + T22).

Past langchain-openai 1.1.9 two things an eval arm sees move with no code of ours changing:
a tool message's text blocks go out with only ``type`` and ``text`` (1.1.9 also sent
LangChain's block ``id``, so every multi-step request's bytes and prompt-cache prefix
move; 1.1.14 already strips it), and ``prompt_tokens_details.cache_write_tokens`` is
reported as cache-creation input tokens (always 0 on 1.1.9 and 1.1.14; the locked 1.3.5
reports it). Both are pinned here through the production construction
site and ``FakeChatCompletionsEndpoint`` — zero network — so a campaign runner sees the
change described before running into it: arms are comparable only within one dependency
set. The harness extra is required, so this module runs locally, not in GitHub CI.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("langchain_openai")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.content import create_text_block

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NO_BEARER
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    build_chat_model,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.model_usage import message_usages
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import chat_completion_body
from ._reasoning_fakes import FakeChatCompletionsEndpoint

_URL = "http://llm.test/v1"
_TOOL_TEXT = "def f(x: int) -> int"


def _model(endpoint: FakeChatCompletionsEndpoint):
    """The production construction site (``build_chat_model``) over the fake endpoint."""
    cfg = LlmConnectionConfig.model_validate({"base_url": _URL, "model": "m"})
    connection = resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )
    return build_chat_model(connection, NO_BEARER, transport=endpoint.transport)


def _endpoint(body: dict) -> FakeChatCompletionsEndpoint:
    return FakeChatCompletionsEndpoint(json_body=json.dumps(body).encode())


def _tool_round() -> list:
    """One tool round as the agent replays it: the MCP adapter builds the tool result's
    content with ``create_text_block`` (langchain-mcp-adapters), which stamps a block id."""
    call = {"name": "lookup_symbol", "args": {"name": "pkg.f"}, "id": "call_1"}
    result = ToolMessage(
        content=[create_text_block(text=_TOOL_TEXT)], tool_call_id="call_1", name="lookup_symbol"
    )
    return [HumanMessage("q"), AIMessage("", tool_calls=[call]), result]


def test_a_tool_round_sends_tool_text_blocks_with_only_type_and_text() -> None:
    history = _tool_round()
    assert str(history[-1].content[0]["id"]).startswith("lc_")  # the id exists on our side
    endpoint = _endpoint(chat_completion_body("done"))

    asyncio.run(_model(endpoint).ainvoke(history))

    (request,) = endpoint.requests
    assert request["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": [{"type": "text", "text": _TOOL_TEXT}],
    }


def test_cache_write_tokens_become_cache_creation_input_tokens() -> None:
    body = chat_completion_body("ok")
    body["usage"] = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
    }

    reply = asyncio.run(_model(_endpoint(body)).ainvoke("q"))

    (record,) = message_usages([reply])
    assert record.cache_creation_input_tokens == 10
    assert record.cache_read_input_tokens == 40
    assert (record.input_tokens, record.output_tokens) == (100, 20)
