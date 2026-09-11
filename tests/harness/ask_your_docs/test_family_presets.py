"""The model card's recommended sampling values (S7): pure, streamlit-free, eval-invisible."""

from __future__ import annotations

import inspect

import pytest

from pydocs_mcp.harness.ask_your_docs import family_presets
from pydocs_mcp.harness.ask_your_docs.family_presets import (
    PRESET_TABLE,
    ThinkingPreset,
    family_preset,
    preset_for,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

from ._connection_fakes import FakeModelsEndpoint

_OR_PARAMS = ("temperature", "top_p", "reasoning_effort")


@pytest.mark.parametrize("model", ["qwen/qwen3.8-27b", "Qwen3.8-27B", "qwen3.8-flash"])
def test_a_qwen38_model_hits_the_card_row(model: str) -> None:
    assert family_preset(model) is PRESET_TABLE["qwen3.8"]


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "gpt-5-mini", "acme-chat-7b"])
def test_a_model_the_card_does_not_cover_has_no_preset(model: str) -> None:
    assert family_preset(model) is None


def test_the_preset_carries_the_cards_two_modes() -> None:
    preset = PRESET_TABLE["qwen3.8"]
    assert dict(preset.values(thinking_off=False)) == {"temperature": 1.0, "top_p": 0.95}
    assert dict(preset.values(thinking_off=True)) == {"temperature": 0.7, "top_p": 0.80}


def test_a_listing_declared_default_is_netted_out_of_the_preset() -> None:
    # Tier 2 beats tier 3: a number the deployment already applies stays a placeholder,
    # so the dialog never re-sends it (settings_view's "a placeholder is never sent").
    entry = FakeModelsEndpoint.openrouter_entry(
        "qwen/qwen3.8-27b", _OR_PARAMS, default_parameters={"temperature": 0.6, "top_p": None}
    )
    preset = preset_for("qwen/qwen3.8-27b", entry)
    assert preset is not None
    assert dict(preset.values(thinking_off=False)) == {"top_p": 0.95}
    assert dict(preset.values(thinking_off=True)) == {"top_p": 0.80}


def test_a_listing_without_declared_numbers_leaves_the_preset_whole() -> None:
    entry = FakeModelsEndpoint.openrouter_entry("qwen/qwen3.8-27b", _OR_PARAMS)
    assert preset_for("qwen/qwen3.8-27b", entry) is PRESET_TABLE["qwen3.8"]
    assert preset_for("qwen/qwen3.8-27b", None) is PRESET_TABLE["qwen3.8"]
    assert preset_for("Qwen/Qwen3-8B", entry) is None


def test_the_card_values_phase_one_cannot_route_are_recorded_for_d4() -> None:
    # top_k / min_p / presence_penalty / repetition_penalty need the D4 extra_body route;
    # they are recorded so that PR can pick them up, and no code path sends them today.
    preset = PRESET_TABLE["qwen3.8"]
    assert dict(preset.not_yet_routable) == {
        "top_k": (20, 20),
        "min_p": (0.0, 0.0),
        "presence_penalty": (0.0, 1.5),
        "repetition_penalty": (1.0, 1.0),
    }
    for key in preset.not_yet_routable:
        with pytest.raises(ValueError, match="is not configurable"):
            ChatParamsConfig.model_validate({key: 1})


def test_only_routable_keys_are_pre_filled() -> None:
    preset = PRESET_TABLE["qwen3.8"]
    routable = set(ChatParamsConfig.model_fields)
    for thinking_off in (False, True):
        assert set(preset.values(thinking_off=thinking_off)) <= routable
    assert not set(preset.not_yet_routable) & routable


def test_the_preset_module_is_pure() -> None:
    source = inspect.getsource(family_presets)
    assert "streamlit" not in source and "httpx" not in source
    assert isinstance(PRESET_TABLE["qwen3.8"], ThinkingPreset)
    # One place to edit for the next Qwen generation.
    assert "https://huggingface.co/Qwen/Qwen3.8-27B" in source
