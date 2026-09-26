"""The chat model's request settings: timeout, retries and the OpenRouter provider route.

Spec 2026-09-25 step 2b (Q48(c)/(d)): the pinned ``ask_your_docs.llm`` block is their
single source. The resolver carries them onto the connection and refuses a route
outside the OpenRouter wire profile; ``block_request_kwargs`` hands them to the chat
factory, which sends the route in the request body. Unset, they leave the ``ChatOpenAI``
kwargs byte-identical (the AC-19 spy). The YAML side is pinned by
``tests/test_config_ask_your_docs_llm_request.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("langchain_openai")

import langchain_openai

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.chat_request import (
    ProviderRoutingProfileError,
    block_request_kwargs,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    bearer_for_connection,
    build_chat_model,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import RecordingTransport

_OPENROUTER = "https://openrouter.ai/api/v1"
_ROUTE = {"order": ["deepinfra/bf16"], "allow_fallbacks": False}
_ROUTED_BLOCK = {
    "base_url": _OPENROUTER,
    "model": "qwen/qwen3.8-27b",
    "timeout_seconds": 300,
    "max_retries": 2,
    "provider_routing": _ROUTE,
}
# The block file every paid ask arm pins with --llm-block (Q5): its values are the ones
# that must reach the model.
_PINNED_BLOCK_FILE = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "configs"
    / "ask_openrouter_qwen3_8_27b_llm.yaml"
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_bearer_registry()
    yield
    clear_bearer_registry()


def _connection(block: dict | None, dialog: ConnectionOverride | None = None):
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), dialog or ConnectionOverride(), config_path=None
    )


def _built_kwargs(monkeypatch: pytest.MonkeyPatch, connection, **factory_kwargs) -> dict:
    seen: list[dict] = []

    class _SpyChatOpenAI:
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _SpyChatOpenAI)
    build_chat_model(connection, NoBearer(), **factory_kwargs)
    return seen[0]


# --- the resolver carries them, and gates the route -----------------------------------


def test_the_resolver_carries_the_request_settings_onto_the_connection() -> None:
    connection = _connection(_ROUTED_BLOCK)

    assert (connection.timeout_seconds, connection.max_retries) == (300.0, 2)
    assert connection.provider_routing is not None
    assert connection.provider_routing.order == ("deepinfra/bf16",)


def test_no_block_resolves_no_request_settings() -> None:
    connection = _connection(None)

    assert (connection.timeout_seconds, connection.max_retries) == (None, None)
    assert connection.provider_routing is None and block_request_kwargs(connection) == {}


@pytest.mark.parametrize(
    ("endpoint", "profile"),
    [
        ({"base_url": "http://llm.internal/v1"}, "generic"),
        ({"base_url": "http://gpu-box:8000/v1", "provider": "vllm"}, "vllm"),
        ({"base_url": None}, "openai"),
    ],
    ids=["an unknown host", "a declared vllm server", "the vendor default"],
)
def test_a_route_outside_the_openrouter_profile_is_refused(endpoint: dict, profile: str) -> None:
    """The ``provider`` body field means something to OpenRouter only; elsewhere it is refused
    before any request, naming the key and the profile the endpoint resolved to."""
    with pytest.raises(ProviderRoutingProfileError) as excinfo:
        _connection({"model": "m", "provider_routing": _ROUTE, **endpoint})

    message = str(excinfo.value)
    assert "ask_your_docs.llm.provider_routing" in message and repr(profile) in message


def test_a_declared_openrouter_provider_accepts_the_route_on_any_host() -> None:
    """The declared provider wins over the host — the same rule the rest of the wire follows."""
    block = {"base_url": "https://gateway.internal/v1", "provider": "openrouter", "model": "m"}
    connection = _connection({**block, "provider_routing": _ROUTE})
    assert connection.provider_routing is not None


def test_the_route_follows_the_endpoint_the_dialog_points_at() -> None:
    """A dialog override that leaves OpenRouter takes the pinned route's refusal with it."""
    local = ConnectionOverride(base_url="http://localhost:8000/v1")
    with pytest.raises(ProviderRoutingProfileError):
        _connection(_ROUTED_BLOCK, dialog=local)


# --- the factory sends them -----------------------------------------------------------


def test_unset_request_settings_leave_the_chat_model_kwargs_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-19 byte identity: a block without the three keys adds nothing to today's build."""
    connection = _connection({"base_url": _OPENROUTER, "model": "m"})
    assert block_request_kwargs(connection) == {}

    todays = _built_kwargs(monkeypatch, connection)
    carried = _built_kwargs(monkeypatch, connection, **block_request_kwargs(connection))

    assert set(carried) == set(todays)
    assert not {"timeout", "max_retries", "extra_body"} & set(carried)


def test_set_request_settings_reach_the_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection(_ROUTED_BLOCK)

    kwargs = _built_kwargs(monkeypatch, connection, **block_request_kwargs(connection))

    assert (kwargs["timeout"], kwargs["max_retries"]) == (300.0, 2)
    assert kwargs["extra_body"] == {"provider": _ROUTE}


def test_the_route_lands_in_the_request_body() -> None:
    """End to end on the real SDK: the provider object is a top-level body field."""
    connection = _connection(_ROUTED_BLOCK)
    recorder = RecordingTransport([200])
    llm = build_chat_model(
        connection, NoBearer(), transport=recorder.transport, **block_request_kwargs(connection)
    )

    asyncio.run(llm.ainvoke("hi"))

    body = json.loads(recorder.requests[-1].content)
    assert body["provider"] == _ROUTE


def test_the_pinned_block_files_settings_reach_the_model_and_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file the paid arms pin is the single source of these three values (Q48)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    block = yaml.safe_load(_PINNED_BLOCK_FILE.read_text(encoding="utf-8"))
    connection = _connection({**block, "model": "qwen/qwen3.8-27b"})
    request = block_request_kwargs(connection)

    kwargs = _built_kwargs(monkeypatch, connection, **request)
    assert (kwargs["timeout"], kwargs["max_retries"]) == (300.0, 2)
    assert kwargs["extra_body"] == {"provider": _ROUTE}

    monkeypatch.undo()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    recorder = RecordingTransport([200])
    bearer = bearer_for_connection(connection)
    llm = build_chat_model(connection, bearer, transport=recorder.transport, **request)
    asyncio.run(llm.ainvoke("hi"))
    assert json.loads(recorder.requests[-1].content)["provider"] == _ROUTE
