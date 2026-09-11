"""Provider profiles (model-params v2 §2): the pure wire profile, the display profile, the family table."""

from __future__ import annotations

import inspect

import pytest

from pydocs_mcp.harness.ask_your_docs import provider_profiles
from pydocs_mcp.harness.ask_your_docs.provider_profiles import (
    FAMILY_TABLE,
    THINKING_MAP_VERSION,
    DisplayProfile,
    ProviderProfile,
    SamplingRule,
    display_profile,
    family_row,
    last_segment,
    longest_prefix_key,
    wire_profile,
)
from pydocs_mcp.retrieval.llm_clients import openai as openai_client
from pydocs_mcp.retrieval.llm_clients.reasoning_models import REASONING_MODEL_PREFIXES

from ._connection_fakes import FakeModelGroupInfo, FakeModelsEndpoint

_OPENROUTER = "https://openrouter.ai/api/v1"
_LOCAL = "http://localhost:8000/v1"


def test_declared_provider_beats_the_host() -> None:
    assert wire_profile("vllm", _OPENROUTER) is ProviderProfile.VLLM
    assert wire_profile("generic", "https://api.openai.com/v1") is ProviderProfile.GENERIC


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (_OPENROUTER, ProviderProfile.OPENROUTER),
        ("https://OpenRouter.ai/api/v1", ProviderProfile.OPENROUTER),
        ("https://api.openai.com/v1", ProviderProfile.OPENAI),
        (None, ProviderProfile.OPENAI),  # null base_url = the vendor default
        (_LOCAL, ProviderProfile.GENERIC),
        ("https://openrouter.ai.evil.example/v1", ProviderProfile.GENERIC),
    ],
)
def test_host_rules(base_url: str | None, expected: ProviderProfile) -> None:
    assert wire_profile("auto", base_url) is expected


def test_wire_profile_never_reads_a_listing() -> None:
    assert list(inspect.signature(wire_profile).parameters) == ["declared", "base_url"]


def test_owned_by_vllm_is_display_vllm_while_wire_stays_generic() -> None:
    wire = wire_profile("auto", _LOCAL)
    entry = FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B")
    assert wire is ProviderProfile.GENERIC
    assert display_profile(wire, entry, None) == DisplayProfile(ProviderProfile.VLLM)


def test_litellm_proxy_in_front_of_vllm_is_litellm() -> None:
    # LiteLLM rewrites owned_by, and its /model_group/info answers: the gateway's rules apply.
    entry = {"id": "Qwen/Qwen3-8B", "owned_by": "openai"}
    group_info = FakeModelGroupInfo("Qwen/Qwen3-8B", providers=("hosted_vllm",)).row()
    shown = display_profile(ProviderProfile.GENERIC, entry, group_info)
    assert shown == DisplayProfile(ProviderProfile.LITELLM)


@pytest.mark.parametrize(
    "entry",
    [FakeModelsEndpoint.llamacpp_entry("qwen3-8b"), FakeModelsEndpoint.ollama_entry("qwen3:8b")],
)
def test_llamacpp_and_ollama_set_the_cap_ignoring_flavour(entry: dict) -> None:
    shown = display_profile(ProviderProfile.GENERIC, entry, None)
    assert shown == DisplayProfile(ProviderProfile.GENERIC, ignores_output_cap=True)


def test_plain_generic_endpoint_keeps_the_cap() -> None:
    shown = display_profile(ProviderProfile.GENERIC, {"id": "acme-chat-7b"}, None)
    assert shown == DisplayProfile(ProviderProfile.GENERIC)


def test_a_decided_wire_profile_is_never_re_routed_by_detection() -> None:
    entry = FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B")
    group_info = FakeModelGroupInfo().row()
    for wire in (ProviderProfile.OPENAI, ProviderProfile.OPENROUTER, ProviderProfile.LITELLM):
        assert display_profile(wire, entry, group_info).profile is wire


@pytest.mark.parametrize("declared_profile", list(ProviderProfile))
def test_a_declared_provider_is_final(declared_profile: ProviderProfile) -> None:
    # §2 step 1: the declared provider decides, including `generic` — the one-line escape
    # hatch out of the vLLM mask. Only an `auto` wire is ever refined by detection.
    entry = FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B")
    group_info = FakeModelGroupInfo().row()
    shown = display_profile(declared_profile, entry, group_info, declared=True)
    assert shown.profile is declared_profile


def test_a_declared_generic_keeps_the_cap_ignoring_flavour() -> None:
    # The sub-flavour is not a re-route: an Ollama server ignores the cap either way.
    entry = FakeModelsEndpoint.ollama_entry("qwen3:8b")
    shown = display_profile(ProviderProfile.GENERIC, entry, None, declared=True)
    assert shown == DisplayProfile(ProviderProfile.GENERIC, ignores_output_cap=True)


def test_family_row_longest_prefix_wins_and_matches_the_last_segment() -> None:
    assert family_row("gpt-5.1-mini", ProviderProfile.OPENAI) is FAMILY_TABLE["gpt-5."]
    assert family_row("openai/gpt-5-mini", ProviderProfile.OPENROUTER) is FAMILY_TABLE["gpt-5"]
    assert family_row("acme-chat-7b", ProviderProfile.OPENAI) is None


@pytest.mark.parametrize("model", ["gpt-5-chat-latest", "gpt-5.1-chat-latest", "openai/gpt-5-chat"])
def test_gpt5_chat_is_not_a_reasoning_row(model: str) -> None:
    # LangChain's own carve-out (validate_temperature): a gpt-5*chat* model takes
    # temperature and refuses reasoning_effort — the opposite of the gpt-5 rows.
    row = family_row(model, ProviderProfile.OPENAI)
    assert row is not None
    assert (row.thinking, row.sampling) == ((), SamplingRule.ALWAYS)


def test_vllm_rows_apply_only_on_the_vllm_profile() -> None:
    assert family_row("Qwen/Qwen3-8B", ProviderProfile.VLLM) is FAMILY_TABLE["qwen3"]
    assert family_row("Qwen/Qwen3-8B", ProviderProfile.OPENROUTER) is None


def test_qwen38_wins_on_length_and_applies_off_vllm_too() -> None:
    # "qwen3.8-27b" also startswith "qwen3", so the longer key decides. The row spells the
    # MODEL's effort vocabulary (xhigh | medium | low), not a vLLM chat-template feature,
    # so it is not vllm_only and a generic / OpenRouter Qwen3.8 gets the same options.
    for profile in (ProviderProfile.VLLM, ProviderProfile.OPENROUTER, ProviderProfile.GENERIC):
        assert family_row("qwen/qwen3.8-27b", profile) is FAMILY_TABLE["qwen3.8"]
    row = FAMILY_TABLE["qwen3.8"]
    assert (row.on_off, row.vllm_only, row.sampling) == (False, False, SamplingRule.ALWAYS)


def test_one_matcher_serves_every_family_rule() -> None:
    # The prefix rule has a single implementation; family_row and the preset table share it.
    assert last_segment("Qwen/Qwen3.8-27B") == "qwen3.8-27b"
    assert longest_prefix_key("qwen3.8-27b", ("qwen3", "qwen3.8")) == "qwen3.8"
    assert longest_prefix_key("acme-chat-7b", FAMILY_TABLE) is None


def test_family_table_and_openai_client_share_one_prefix_source() -> None:
    assert set(REASONING_MODEL_PREFIXES) <= set(FAMILY_TABLE)
    assert not hasattr(openai_client, "_REASONING_MODEL_PREFIXES")
    assert openai_client.REASONING_MODEL_PREFIXES is REASONING_MODEL_PREFIXES


def test_thinking_map_version_is_one_and_the_module_is_light() -> None:
    assert THINKING_MAP_VERSION == 1
    source = inspect.getsource(provider_profiles)
    assert "langchain" not in source and "streamlit" not in source and "httpx" not in source
