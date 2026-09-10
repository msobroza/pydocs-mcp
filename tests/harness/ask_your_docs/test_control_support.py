"""Per-control support (model-params v2 §3 + owner D5/D6): pure, network-free, hide-only."""

from __future__ import annotations

import httpx
import openai
import pytest

from pydocs_mcp.harness.ask_your_docs.control_support import (
    ControlSupport,
    rejected_control_from_error,
    support_for,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import DisplayProfile, ProviderProfile
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ThinkingLevel as T

from ._connection_fakes import FakeModelGroupInfo, FakeModelsEndpoint

_OR = DisplayProfile(ProviderProfile.OPENROUTER)
_OPENAI = DisplayProfile(ProviderProfile.OPENAI)
_VLLM = DisplayProfile(ProviderProfile.VLLM)
_LITELLM = DisplayProfile(ProviderProfile.LITELLM)
_GENERIC = DisplayProfile(ProviderProfile.GENERIC)
_ALL_FIVE = ("Auto", "Off", "Low", "Medium", "High")
_OR_PARAMS = ("max_tokens", "temperature", "top_p", "seed", "reasoning_effort", "reasoning")


def _qwen_on_openrouter(**overrides: object) -> dict:
    kwargs: dict = {"efforts": ("xhigh", "medium", "low"), "mandatory": False, **overrides}
    return FakeModelsEndpoint.openrouter_entry("qwen/qwen3.8-27b", _OR_PARAMS, **kwargs)


# ── OpenRouter ────────────────────────────────────────────────────────────────


def test_openrouter_efforts_are_intersected_never_coerced() -> None:
    support = support_for(_OR, "qwen/qwen3.8-27b", _qwen_on_openrouter())
    assert support.thinking_labels == ("Auto", "Low", "Medium")  # no High, no Off


def test_openrouter_off_needs_none_listed_and_not_mandatory() -> None:
    efforts = ("none", "low", "medium", "high")
    listed = support_for(_OR, "m", _qwen_on_openrouter(efforts=efforts))
    mandatory = support_for(_OR, "m", _qwen_on_openrouter(efforts=efforts, mandatory=True))
    assert listed.thinking_labels == _ALL_FIVE
    assert "Off" not in mandatory.thinking_labels


def test_openrouter_reasoning_only_model_hides_thinking() -> None:
    entry = FakeModelsEndpoint.openrouter_entry("qwen/qwen3.8-flash", ("temperature", "reasoning"))
    assert support_for(_OR, "qwen/qwen3.8-flash", entry).thinking_options == ()


def test_openrouter_never_hides_max_output_tokens_d6() -> None:
    entry = FakeModelsEndpoint.openrouter_entry("mistral/nemo", ("temperature",))
    support = support_for(_OR, "mistral/nemo", entry, learned=frozenset({"max_tokens"}))
    assert support.show_max_tokens is True
    assert (support.show_top_p, support.show_seed) == (False, False)


def test_openrouter_openai_model_uses_its_listing() -> None:
    entry = FakeModelsEndpoint.openrouter_entry(
        "openai/gpt-5-mini",
        ("max_tokens", "reasoning_effort", "seed"),
        efforts=("high", "medium", "low", "minimal"),
        mandatory=True,
    )
    support = support_for(_OR, "openai/gpt-5-mini", entry)
    assert support.thinking_labels == ("Auto", "Low", "Medium", "High")
    assert support.show_temperature is False


# ── LiteLLM ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("efforts", "labels"),
    [
        (None, _ALL_FIVE),
        ((), ()),
        (("low", "high"), ("Auto", "Low", "High")),
        (("none", "medium"), ("Auto", "Off", "Medium")),
    ],
)
def test_litellm_efforts_none_all_empty_hides_tuple_intersects(efforts, labels) -> None:
    row = FakeModelGroupInfo(supported_reasoning_efforts=efforts).row()
    assert support_for(_LITELLM, "team-sonnet", None, row).thinking_labels == labels


def test_litellm_reasoning_effort_not_listed_hides_thinking_and_unlisted_params() -> None:
    row = FakeModelGroupInfo(
        providers=("openai",), supported_openai_params=("temperature", "max_tokens")
    ).row()
    support = support_for(_LITELLM, "Qwen/Qwen3-8B", None, row)
    assert support.thinking_options == ()
    assert (support.show_top_p, support.show_seed, support.show_max_tokens) == (False, False, True)


def test_litellm_ceiling_is_max_output_tokens() -> None:
    row = FakeModelGroupInfo(max_output_tokens=64000.0).row()
    assert support_for(_LITELLM, "team-sonnet", None, row).max_tokens_ceiling == 64000


def test_anthropic_litellm_hides_sampling_only_while_thinking_is_active() -> None:
    support = support_for(_LITELLM, "team-sonnet", None, FakeModelGroupInfo().row())
    for thinking in (None, T.OFF):
        assert support.temperature_shown(thinking) and support.top_p_shown(thinking)
    for thinking in (T.LOW, T.MEDIUM, T.HIGH):
        assert not support.temperature_shown(thinking) and not support.top_p_shown(thinking)


# ── OpenAI family table ───────────────────────────────────────────────────────


def test_openai_gpt5_has_no_off_and_no_temperature() -> None:
    support = support_for(_OPENAI, "gpt-5-mini")
    assert support.thinking_labels == ("Auto", "Low", "Medium", "High")
    assert not support.temperature_shown(None) and not support.top_p_shown(T.LOW)


def test_openai_gpt51_shows_temperature_only_with_off() -> None:
    support = support_for(_OPENAI, "gpt-5.1")
    assert support.thinking_labels == _ALL_FIVE
    assert support.temperature_shown(T.OFF)
    assert not support.temperature_shown(None) and not support.temperature_shown(T.LOW)


def test_openai_gpt4o_hides_thinking_keeps_sampling() -> None:
    support = support_for(_OPENAI, "gpt-4o-mini")
    assert support.thinking_options == () and support.temperature_shown(None)


def test_longest_prefix_wins_and_last_segment_matches() -> None:
    assert "Off" in support_for(_OPENAI, "gpt-5.2-pro").thinking_labels  # "gpt-5." beats "gpt-5"
    assert support_for(_GENERIC, "openai/gpt-5-mini").thinking_labels == (
        "Auto",
        "Low",
        "Medium",
        "High",
    )


# ── vLLM (D5: never Off) ──────────────────────────────────────────────────────


def test_vllm_qwen3_is_auto_on() -> None:
    support = support_for(_VLLM, "Qwen/Qwen3-8B", FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B"))
    assert support.thinking_labels == ("Auto", "On")
    assert support.thinking_options == (T.AUTO, T.MEDIUM)  # On stores medium (D8)


@pytest.mark.parametrize("model", ["openai/gpt-oss-20b", "meta-llama/Llama-3.1-8B", "gpt-5.1"])
def test_no_vllm_row_ever_offers_off_d5(model: str) -> None:
    labels = support_for(_VLLM, model).thinking_labels
    assert labels == ("Auto", "Low", "Medium", "High")


def test_vllm_ceiling_is_max_model_len() -> None:
    entry = FakeModelsEndpoint.vllm_entry("Qwen/Qwen3-8B", max_model_len=40960)
    assert support_for(_VLLM, "Qwen/Qwen3-8B", entry).max_tokens_ceiling == 40960


# ── generic, cap-ignoring servers, learned rejections ─────────────────────────


def test_generic_shows_everything() -> None:
    support = support_for(_GENERIC, "acme-chat-7b")
    assert support.thinking_labels == _ALL_FIVE
    assert (support.show_temperature, support.show_max_tokens) == (True, True)
    assert (support.show_top_p, support.show_seed, support.max_tokens_ceiling) == (True, True, None)


def test_llamacpp_and_ollama_hide_max_output_tokens() -> None:
    capless = DisplayProfile(ProviderProfile.GENERIC, ignores_output_cap=True)
    assert support_for(capless, "qwen3:8b").show_max_tokens is False


@pytest.mark.parametrize(
    ("control", "field"),
    [("temperature", "show_temperature"), ("seed", "show_seed"), ("top_p", "show_top_p")],
)
def test_a_learned_rejection_hides_the_control(control: str, field: str) -> None:
    assert getattr(support_for(_GENERIC, "m", learned=frozenset({control})), field) is False


def test_a_learned_thinking_rejection_hides_thinking() -> None:
    assert (
        support_for(_VLLM, "Qwen/Qwen3-8B", learned=frozenset({"thinking"})).thinking_options == ()
    )


def _options(support: ControlSupport) -> set[str]:
    shown = {
        name
        for name in ("temperature", "max_tokens", "top_p", "seed")
        if getattr(support, f"show_{name}")
    }
    return shown | {f"thinking:{label}" for label in support.thinking_options}


@pytest.mark.parametrize("profile", list(ProviderProfile))
def test_live_signals_only_remove_and_results_are_deterministic(profile: ProviderProfile) -> None:
    display = DisplayProfile(profile)
    entry = _qwen_on_openrouter() | {"owned_by": "vllm", "max_model_len": 2048}
    row = FakeModelGroupInfo().row()
    bare = support_for(display, "Qwen/Qwen3-8B")
    live = support_for(display, "Qwen/Qwen3-8B", entry, row, frozenset({"seed"}))
    assert _options(live) <= _options(support_for(_GENERIC, "Qwen/Qwen3-8B"))
    assert _options(live) <= _options(bare)
    assert live == support_for(display, "Qwen/Qwen3-8B", entry, row, frozenset({"seed"}))


# ── the rejection parser ──────────────────────────────────────────────────────


def _bad_request(message: str, param: str | None = None) -> openai.BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "http://x/v1/chat/completions"))
    return openai.BadRequestError(
        message, response=response, body={"message": message, "param": param}
    )


def test_parser_reads_bad_request_param() -> None:
    exc = _bad_request("Unsupported value", param="temperature")
    assert rejected_control_from_error(exc, {"temperature": 0.2, "seed": 7}) == "temperature"


def test_parser_reads_the_litellm_phrasings() -> None:
    listed = _bad_request("openai does not support parameters: ['reasoning_effort'], for model=x")
    assignment = _bad_request(
        "gpt-5-mini doesn't support temperature=0.2 while reasoning is active"
    )
    assert rejected_control_from_error(listed, {"reasoning_effort": "low"}) == "thinking"
    assert rejected_control_from_error(assignment, {"temperature": 0.2}) == "temperature"


def test_parser_reads_a_quoted_name_and_maps_the_wire_name() -> None:
    exc = _bad_request("Unrecognized request argument supplied: 'max_completion_tokens'")
    assert rejected_control_from_error(exc, {"max_completion_tokens": 64}) == "max_tokens"


def test_parser_ignores_names_that_were_not_sent() -> None:
    exc = _bad_request("'top_p' is not supported", param="top_p")
    assert rejected_control_from_error(exc, {"temperature": 0.2}) is None


def test_parser_ignores_non_400_errors() -> None:
    response = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    exc = openai.InternalServerError("'temperature' broke", response=response, body=None)
    assert rejected_control_from_error(exc, {"temperature": 0.2}) is None
