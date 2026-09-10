"""Reasoning capture over the locked langchain-openai (activity panel, PROPOSAL §4, TDD 1).

The three recorded OpenRouter bodies (A non-streaming + tool call, B streaming, C
streaming + tool call) replay through ``FakeChatCompletionsEndpoint`` — zero network.
AC-19's kwargs spy (``test_chat_model_factory``) stays green unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging

import pytest

pytest.importorskip("langchain_openai")

import langchain_openai
from langchain_openai.chat_models.base import _convert_message_to_dict

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    build_chat_model,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.reasoning_capture import (
    OVERRIDDEN_METHODS,
    REASONING_KWARG,
    REDACTED_KWARG,
    ThinkTagSplitter,
    reasoning_chat_model_class,
    reasoning_is_redacted,
    reasoning_text_from_payload,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._reasoning_fakes import FakeChatCompletionsEndpoint, recorded_body, sse_body, wire_reasoning

_URL = "http://llm.test/v1"
_A_REASONING = (
    "The user is asking me to use lookup_symbol to find the signature of pkg.f, and then stop.\n"
)
_ENCRYPTED = [{"type": "reasoning.encrypted", "data": "opaque", "index": 0}]


def _model(endpoint: FakeChatCompletionsEndpoint):
    """The production construction site; ``astream`` sends ``"stream": true`` by itself."""
    cfg = LlmConnectionConfig.model_validate({"base_url": _URL, "model": "m"})
    connection = resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )
    return build_chat_model(connection, NoBearer(), transport=endpoint.transport)


def _invoke(llm):
    return asyncio.run(llm.ainvoke("q"))


def _stream(llm):
    async def merged():
        total = None
        async for chunk in llm.astream("q"):
            total = chunk if total is None else total + chunk
        return total

    return asyncio.run(merged())


def _json_message(message: dict) -> bytes:
    choice = {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}
    body = {"id": "g", "object": "chat.completion", "model": "m", "choices": [choice]}
    return json.dumps(body).encode()


def _delta_chunk(delta: dict) -> dict:
    choice = {"index": 0, "delta": delta, "finish_reason": None}
    return {"id": "g", "object": "chat.completion.chunk", "model": "m", "choices": [choice]}


# ── payload readers ──


def test_first_string_field_wins_and_is_never_concatenated_with_details() -> None:
    both = {"reasoning": "plan", "reasoning_details": [{"type": "reasoning.text", "text": "plan"}]}
    assert reasoning_text_from_payload(both) == "plan"
    assert reasoning_text_from_payload({"reasoning_content": "a", "reasoning": "b"}) == "a"


def test_details_are_the_fallback_when_no_string_field() -> None:
    details = [
        {"type": "reasoning.summary", "summary": "short "},
        {"type": "reasoning.text", "text": "long"},
        {"type": "reasoning.encrypted", "data": "x"},
        "not-a-dict",
    ]
    assert reasoning_text_from_payload({"reasoning_details": details}) == "short long"
    assert reasoning_text_from_payload({"content": "answer"}) is None
    assert reasoning_text_from_payload({"reasoning": "", "reasoning_details": "junk"}) is None


def test_encrypted_detail_marks_the_payload_redacted() -> None:
    assert reasoning_is_redacted({"reasoning_details": _ENCRYPTED}) is True
    assert reasoning_is_redacted({"reasoning": "visible"}) is False


# ── the model class (A4 contract + canary) ──


def test_overridden_private_methods_still_exist_upstream() -> None:
    """A4: capture overrides two PRIVATE ChatOpenAI methods; a rename must fail CI loudly."""
    for name in OVERRIDDEN_METHODS:
        method = getattr(langchain_openai.ChatOpenAI, name, None)
        assert callable(method), f"langchain_openai.ChatOpenAI.{name} disappeared"
    chunk_params = list(
        inspect.signature(langchain_openai.ChatOpenAI._convert_chunk_to_generation_chunk).parameters
    )
    result_params = list(
        inspect.signature(langchain_openai.ChatOpenAI._create_chat_result).parameters
    )
    assert chunk_params[:2] == ["self", "chunk"] and result_params[:2] == ["self", "response"]


def test_stock_chat_openai_still_drops_reasoning_canary() -> None:
    """Canary: the day upstream keeps the field itself, this fails — drop the subclass then."""
    endpoint = FakeChatCompletionsEndpoint(json_body=recorded_body("A.json"))
    stock = langchain_openai.ChatOpenAI(
        model="m", base_url=_URL, api_key="no-auth", http_async_client=_client(endpoint)
    )
    assert REASONING_KWARG not in _invoke(stock).additional_kwargs


def _client(endpoint: FakeChatCompletionsEndpoint):
    import httpx

    return httpx.AsyncClient(transport=endpoint.transport)


def test_the_class_is_derived_once_per_base() -> None:
    derived = reasoning_chat_model_class(langchain_openai.ChatOpenAI)
    assert derived is reasoning_chat_model_class(langchain_openai.ChatOpenAI)
    assert issubclass(derived, langchain_openai.ChatOpenAI)
    assert derived is not langchain_openai.ChatOpenAI


def test_a_base_without_the_methods_degrades_with_one_json_log(caplog) -> None:
    class _NoHooks:
        pass

    caplog.set_level(logging.WARNING, logger="pydocs_mcp.harness.ask_your_docs.reasoning_capture")
    assert reasoning_chat_model_class(_NoHooks) is _NoHooks
    assert reasoning_chat_model_class(_NoHooks) is _NoHooks
    records = [json.loads(r.getMessage()) for r in caplog.records]
    assert records == [{"event": "reasoning_capture_unavailable", "base": "_NoHooks"}]


# ── replays through the one construction site ──


def test_non_streaming_recording_a_keeps_reasoning_and_tool_call() -> None:
    message = _invoke(_model(FakeChatCompletionsEndpoint(json_body=recorded_body("A.json"))))
    assert message.additional_kwargs[REASONING_KWARG] == _A_REASONING
    assert [call["name"] for call in message.tool_calls] == ["lookup_symbol"]


@pytest.mark.parametrize("name", ["B.sse", "C.sse"])
def test_streaming_recordings_merge_to_the_exact_wire_text(name: str) -> None:
    body = recorded_body(name)
    merged = _stream(_model(FakeChatCompletionsEndpoint(stream_body=body)))
    assert merged.additional_kwargs[REASONING_KWARG] == wire_reasoning(body)
    assert wire_reasoning(body)  # the recording really carries reasoning


def test_reasoning_content_dialect_streamed_and_not() -> None:
    plain = FakeChatCompletionsEndpoint(
        json_body=_json_message({"content": "ok", "reasoning_content": "vllm-style"})
    )
    assert _invoke(_model(plain)).additional_kwargs[REASONING_KWARG] == "vllm-style"
    chunks = [_delta_chunk({"role": "assistant", "reasoning_content": p}) for p in ("a", "b")]
    streamed = FakeChatCompletionsEndpoint(stream_body=sse_body(chunks))
    assert _stream(_model(streamed)).additional_kwargs[REASONING_KWARG] == "ab"


def test_encrypted_only_reasoning_sets_the_redacted_flag() -> None:
    body = _json_message({"content": "ok", "reasoning_details": _ENCRYPTED})
    kwargs = _invoke(_model(FakeChatCompletionsEndpoint(json_body=body))).additional_kwargs
    assert kwargs[REDACTED_KWARG] is True and REASONING_KWARG not in kwargs


def test_captured_reasoning_is_never_sent_back_to_the_model() -> None:
    message = _invoke(_model(FakeChatCompletionsEndpoint(json_body=recorded_body("A.json"))))
    assert REASONING_KWARG not in _convert_message_to_dict(message)


# ── inline <think> tags (opt-in) ──


def _split(parts: list[str]) -> tuple[str, str]:
    splitter = ThinkTagSplitter()
    for part in parts:
        splitter.feed(part)
    return splitter.finish()


def test_tags_split_across_chunks() -> None:
    assert _split(["<th", "ink>plan A", " then B</thi", "nk>\n\nAnswer: 42"]) == (
        "plan A then B",
        "\n\nAnswer: 42",
    )


def test_closing_tag_without_opener_reclassifies_the_prefix() -> None:
    """Qwen3 / R1 templates pre-fill ``<think>`` in the prompt, so only ``</think>`` streams."""
    assert _split(["plan A then", " B</think>", "\n\nAnswer: 42"]) == (
        "plan A then B",
        "\n\nAnswer: 42",
    )


def test_no_tags_and_a_trailing_partial_tag_stay_answer() -> None:
    assert _split(["Answer: ", "42"]) == ("", "Answer: 42")
    assert _split(["x < y and a <th"]) == ("", "x < y and a <th")


def test_the_reasoning_subclass_receives_reasoning_effort_unchanged() -> None:
    """§7 pin: the capture subclass takes the factory's kwargs as-is — the wire rides through."""
    from pydocs_mcp.harness.ask_your_docs.chat_wire import resolve_wire
    from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
    from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

    wire, _ = resolve_wire(ChatParamsConfig(thinking="high"), ControlSupport())
    endpoint = FakeChatCompletionsEndpoint(json_body=recorded_body("A.json"))
    cfg = LlmConnectionConfig.model_validate({"base_url": _URL, "model": "m"})
    connection = resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )
    llm = build_chat_model(connection, NoBearer(), transport=endpoint.transport, wire=wire)
    assert type(llm) is not langchain_openai.ChatOpenAI and llm.reasoning_effort == "high"
    _invoke(llm)
    assert endpoint.requests[0]["reasoning_effort"] == "high"
