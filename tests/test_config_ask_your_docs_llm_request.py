"""The chat model's request settings in the ``ask_your_docs.llm`` block.

``timeout_seconds``, ``max_retries`` and ``provider_routing`` — the pinned block
file is their single source (spec 2026-09-25 step 2b, Q48(c)/(d)). Core-suite
tests: pydantic only, no [harness-ask-your-docs] extra needed. Where the values
go (the chat model, the request body, the OpenRouter-only gate) is pinned by
``tests/harness/ask_your_docs/test_chat_request.py``.
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

_ROUTE_SLUG = "deepinfra/bf16"


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Hermeticity (repo convention): no PYDOCS_* from the shell, no cwd config."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


def _load_llm_block(tmp_path: Path, block: str) -> LlmConnectionConfig:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(f"ask_your_docs:\n  llm:\n{block}", encoding="utf-8")
    llm = AppConfig.load(explicit_path=overlay).ask_your_docs.llm
    assert llm is not None
    return llm


def _rendered(excinfo) -> str:
    """Both surfaces a startup ValidationError reaches stderr / the logs through."""
    error = excinfo.value
    frames = traceback.format_exception(type(error), error, error.__traceback__)
    return str(error) + "".join(frames)


def test_the_request_settings_are_unset_by_default() -> None:
    """None sends nothing: a block without them keeps the client's own bounds and route."""
    block = LlmConnectionConfig()
    assert (block.timeout_seconds, block.max_retries, block.provider_routing) == (None, None, None)


def test_the_request_settings_round_trip_from_yaml(tmp_path: Path) -> None:
    llm = _load_llm_block(
        tmp_path,
        "    base_url: https://openrouter.ai/api/v1\n"
        "    timeout_seconds: 300\n"
        "    max_retries: 2\n"
        "    provider_routing:\n"
        f"      order: [{_ROUTE_SLUG}]\n"
        "      allow_fallbacks: false\n",
    )

    assert (llm.timeout_seconds, llm.max_retries) == (300.0, 2)
    assert llm.provider_routing is not None
    assert llm.provider_routing.order == (_ROUTE_SLUG,)
    assert llm.provider_routing.allow_fallbacks is False


def test_a_route_forbids_fallbacks_unless_it_says_otherwise() -> None:
    """A pin that silently fell back to another upstream would not be a pin."""
    block = LlmConnectionConfig.model_validate({"provider_routing": {"order": [_ROUTE_SLUG]}})
    assert block.provider_routing is not None and block.provider_routing.allow_fallbacks is False


@pytest.mark.parametrize(
    "route",
    [
        {"order": []},
        {"order": [""]},
        {"allow_fallbacks": False},
        {"order": [_ROUTE_SLUG], "only": ["deepinfra"]},
    ],
    ids=["empty order", "blank slug", "no order", "unknown key"],
)
def test_a_route_names_at_least_one_upstream_and_nothing_else(route: dict) -> None:
    with pytest.raises(ValidationError) as excinfo:
        LlmConnectionConfig.model_validate({"provider_routing": route})
    assert "provider_routing" in str(excinfo.value)


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("    timeout_seconds: 0\n", "timeout_seconds: got 0, expected a number > 0 (seconds)"),
        ("    timeout_seconds: '300'\n", "timeout_seconds: got a str, expected a number > 0"),
        ("    max_retries: -1\n", "max_retries: got -1, expected an integer >= 0"),
        ("    max_retries: true\n", "max_retries: got a bool, expected an integer >= 0"),
    ],
    ids=["zero timeout", "string timeout", "negative retries", "boolean retries"],
)
def test_a_bad_request_setting_names_its_value_through_the_redaction(
    tmp_path: Path, block: str, message: str
) -> None:
    """This block's inputs are blanked on the way to stderr, so the MESSAGE carries the value."""
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(f"ask_your_docs:\n  llm:\n{block}", encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        AppConfig.load(explicit_path=overlay)

    assert message in _rendered(excinfo)


@pytest.mark.parametrize(
    ("key", "pointer"),
    [
        ("timeout", "set ask_your_docs.llm.timeout_seconds"),
        ("max_retries", "set ask_your_docs.llm.max_retries"),
    ],
)
def test_the_params_spellings_point_at_the_block_keys(key: str, pointer: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ChatParamsConfig.model_validate({key: 30})
    assert pointer in str(excinfo.value)
