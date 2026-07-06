"""AC-1: LlmClient Protocol exposes both async chat() and chat_sync()."""

from __future__ import annotations

import inspect

from pydocs_mcp.retrieval.protocols import ChatMessage, LlmClient


def test_chat_message_typed_dict_shape() -> None:
    """ChatMessage carries role + content for chat-completion APIs."""
    msg: ChatMessage = {"role": "user", "content": "hello"}
    assert msg["role"] == "user"
    assert msg["content"] == "hello"


def test_llm_client_protocol_has_chat_async() -> None:
    """LlmClient.chat is an async method (the production path)."""
    assert hasattr(LlmClient, "chat")
    assert inspect.iscoroutinefunction(LlmClient.chat)


def test_llm_client_protocol_has_chat_sync() -> None:
    """LlmClient.chat_sync is a sync method (the CLI / debug / test path)."""
    assert hasattr(LlmClient, "chat_sync")
    assert not inspect.iscoroutinefunction(LlmClient.chat_sync)


def test_llm_client_protocol_has_model_name() -> None:
    """LlmClient declares model_name so callers can identify the provider
    without peeking into the concrete class."""
    import pydocs_mcp.retrieval.protocols as proto_module

    src = inspect.getsource(proto_module)
    assert "model_name: str" in src
