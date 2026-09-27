"""The chat model's request settings: timeout, retries and the OpenRouter provider route.

Spec 2026-09-25 step 2b (Q48(c)/(d)): the pinned ``ask_your_docs.llm`` block is their
single source. The resolver carries them onto the connection as one
``ChatRequestSettings`` and refuses a route outside the OpenRouter wire profile; its
``chat_factory_kwargs`` hand them to the chat factory, which sends the route in the
request body. Unset, they leave the ``ChatOpenAI`` kwargs byte-identical (the AC-19
spy). The YAML side is pinned by ``tests/test_config_ask_your_docs_llm_request.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("langchain_openai")

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
from pydocs_mcp.harness.ask_your_docs.chat_request import (
    NO_REQUEST_SETTINGS,
    ProviderRoutingProfileError,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    bearer_for_connection,
    build_chat_model,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import RecordingTransport, chat_model_kwargs_built

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
# that must reach the model. Package data of the eval suite, whose tests are a local-only
# gate — so the pin lives in the product suite (the test_prompt_seed_parity precedent).
_PINNED_BLOCK_FILE = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "configs"
    / "ask_openrouter_qwen3_8_27b_llm.yaml"
)
_needs_the_eval_tree = pytest.mark.skipif(
    not _PINNED_BLOCK_FILE.exists(), reason="benchmarks tree not present"
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_bearer_registry()
    yield
    clear_bearer_registry()


def _connection(block: dict | None, dialog: ConnectionOverride | None = None) -> LlmConnection:
    cfg = LlmConnectionConfig.model_validate(block) if block is not None else None
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), dialog or ConnectionOverride(), config_path=None
    )


def _pinned_block_connection() -> LlmConnection:
    block = yaml.safe_load(_PINNED_BLOCK_FILE.read_text(encoding="utf-8"))
    return _connection({**block, "model": "qwen/qwen3.8-27b"})  # --model, both arms


# --- the resolver carries them, and gates the route -----------------------------------


def test_the_resolver_carries_the_request_settings_onto_the_connection() -> None:
    settings = _connection(_ROUTED_BLOCK).request_settings

    assert (settings.timeout_seconds, settings.max_retries) == (300.0, 2)
    assert settings.provider_routing is not None
    assert settings.provider_routing.order == ("deepinfra/bf16",)


def test_no_block_resolves_no_request_settings() -> None:
    settings = _connection(None).request_settings

    assert settings is NO_REQUEST_SETTINGS
    assert settings.chat_factory_kwargs() == {}


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
    assert connection.request_settings.provider_routing is not None


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
    request = connection.request_settings.chat_factory_kwargs()
    assert request == {}

    todays = chat_model_kwargs_built(monkeypatch, connection)
    carried = chat_model_kwargs_built(monkeypatch, connection, **request)

    assert set(carried) == set(todays)
    assert not {"timeout", "max_retries", "extra_body"} & set(carried)


def test_set_request_settings_reach_the_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection(_ROUTED_BLOCK)
    request = connection.request_settings.chat_factory_kwargs()

    kwargs = chat_model_kwargs_built(monkeypatch, connection, **request)

    assert (kwargs["timeout"], kwargs["max_retries"]) == (300.0, 2)
    assert kwargs["extra_body"] == {"provider": _ROUTE}


def test_the_route_lands_in_the_request_body() -> None:
    """End to end on the real SDK: the provider object is a top-level body field."""
    connection = _connection(_ROUTED_BLOCK)
    recorder = RecordingTransport([200])
    request = connection.request_settings.chat_factory_kwargs()
    llm = build_chat_model(connection, NoBearer(), transport=recorder.transport, **request)

    asyncio.run(llm.ainvoke("hi"))

    body = json.loads(recorder.requests[-1].content)
    assert body["provider"] == _ROUTE


@_needs_the_eval_tree
def test_the_pinned_block_files_settings_reach_the_chat_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file the paid arms pin is the single source of these three values (Q48)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # a fixture value, not a key
    connection = _pinned_block_connection()
    request = connection.request_settings.chat_factory_kwargs()

    kwargs = chat_model_kwargs_built(
        monkeypatch, connection, bearer_for_connection(connection), **request
    )

    assert (kwargs["timeout"], kwargs["max_retries"]) == (300.0, 2)
    assert kwargs["extra_body"] == {"provider": _ROUTE}


@_needs_the_eval_tree
def test_the_pinned_block_files_route_lands_in_the_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")  # a fixture value, not a key
    connection = _pinned_block_connection()
    recorder = RecordingTransport([200])
    request = connection.request_settings.chat_factory_kwargs()
    bearer = bearer_for_connection(connection)
    llm = build_chat_model(connection, bearer, transport=recorder.transport, **request)

    asyncio.run(llm.ainvoke("hi"))

    assert json.loads(recorder.requests[-1].content)["provider"] == _ROUTE
