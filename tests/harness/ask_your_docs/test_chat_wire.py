"""resolve_wire: ChatParamsConfig + ControlSupport → exactly what is sent (model-params v2 §3.1,
§5 rule 2 / rule 7, §6 rule 3). Core deps only — pure, network-free."""

from __future__ import annotations

import json
import logging

import pytest

from pydocs_mcp.harness.ask_your_docs import chat_wire as chat_wire_module
from pydocs_mcp.harness.ask_your_docs.chat_wire import (
    NO_WIRE_PARAMS,
    WireParams,
    connection_wire,
    log_chat_params_effective,
    resolve_wire,
    unhonoured_by_tables,
    wire_summary,
)
from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

_ALL_SHOWN = ControlSupport()
_FULL = {"thinking": "low", "temperature": 0.2, "max_tokens": 4096, "top_p": 0.9, "seed": 7}


def _params(**values: object) -> ChatParamsConfig:
    return ChatParamsConfig.model_validate(values)


def _connection(provider: str, params: dict, model: str = "some-model"):
    block = {"base_url": "http://llm.test/v1", "model": model, "provider": provider}
    cfg = LlmConnectionConfig.model_validate({**block, "params": params})
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


@pytest.mark.parametrize(
    ("thinking", "effort"),
    [("off", "none"), ("low", "low"), ("medium", "medium"), ("high", "high")],
)
def test_every_thinking_choice_maps_to_reasoning_effort(thinking: str, effort: str) -> None:
    wire, not_sent = resolve_wire(_params(thinking=thinking), _ALL_SHOWN)
    assert wire.first_class == (("reasoning_effort", effort),) and not_sent == ()
    assert wire.thinking_off is (thinking == "off")


def test_on_for_an_on_off_family_sends_medium() -> None:
    """D8: "On" is a label for the stored medium — the wire is the same as Medium's."""
    on_off = ControlSupport(thinking_on_off=True)
    assert on_off.thinking_labels[-2] == "On"
    wire, _ = resolve_wire(_params(thinking="medium"), on_off)
    assert wire.first_class == (("reasoning_effort", "medium"),)


def test_auto_and_an_empty_block_send_nothing() -> None:
    for params in (_params(thinking="auto"), ChatParamsConfig()):
        wire, not_sent = resolve_wire(params, _ALL_SHOWN)
        assert wire == NO_WIRE_PARAMS and not_sent == ()


def test_every_configured_param_rides_the_wire_in_sorted_pairs() -> None:
    wire, not_sent = resolve_wire(_params(**_FULL), _ALL_SHOWN)
    assert not_sent == ()
    assert wire.first_class == (
        ("max_tokens", 4096),  # LangChain sends it as max_completion_tokens
        ("reasoning_effort", "low"),
        ("seed", 7),
        ("temperature", 0.2),
        ("top_p", 0.9),
    )
    assert wire.chat_model_kwargs() == dict(wire.first_class)


def test_the_phase_one_wire_is_identical_on_every_profile() -> None:
    """Phase 1: the profile only HIDES; for a model no table knows, every profile sends the same."""
    wires = {connection_wire(_connection(p.value, _FULL)) for p in ProviderProfile}
    assert len(wires) == 1
    (wire,) = wires
    assert wire.extra_body is None and dict(wire.first_class)["reasoning_effort"] == "low"


def test_extra_body_is_never_set_in_phase_one() -> None:
    for thinking in ("off", "low", "medium", "high"):
        for profile in ProviderProfile:
            wire = connection_wire(_connection(profile.value, {"thinking": thinking}))
            assert wire.extra_body is None
            assert "extra_body" not in wire.chat_model_kwargs()


def test_wire_params_are_hashable_and_equal_settings_give_equal_keys() -> None:
    first, _ = resolve_wire(_params(**_FULL), _ALL_SHOWN)
    second, _ = resolve_wire(_params(**dict(reversed(list(_FULL.items())))), _ALL_SHOWN)
    other, _ = resolve_wire(_params(temperature=0.3), _ALL_SHOWN)
    assert hash(first) == hash(second) and first == second
    assert len({first, second, other, NO_WIRE_PARAMS}) == 3


def test_a_hidden_saved_value_is_not_sent_and_logged_by_name_only(caplog) -> None:
    """D7: no caption — the only traces are the Test line and this one JSON log line."""
    support = ControlSupport(show_temperature=False, show_seed=False)
    wire, not_sent = resolve_wire(_params(thinking="low", temperature=0.7, seed=4242), support)
    assert wire.first_class == (("reasoning_effort", "low"),)
    assert not_sent == ("temperature", "seed")
    with caplog.at_level(logging.INFO, logger="pydocs-mcp.harness.ask-your-docs"):
        log_chat_params_effective(wire, not_sent)
    (record,) = [r for r in caplog.records if "chat_params_effective" in r.getMessage()]
    assert json.loads(record.getMessage()) == {
        "event": "chat_params_effective",
        "sent": ["thinking"],
        "not_sent": ["temperature", "seed"],
    }
    assert "0.7" not in record.getMessage() and "4242" not in record.getMessage()


def test_connection_wire_logs_once_and_stays_quiet_without_params(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="pydocs-mcp.harness.ask-your-docs"):
        assert connection_wire(_connection("generic", {})) is NO_WIRE_PARAMS
        connection_wire(_connection("vllm", {"thinking": "off", "seed": 1}))
    lines = [r.getMessage() for r in caplog.records if "chat_params_effective" in r.getMessage()]
    assert [json.loads(line) for line in lines] == [
        {"event": "chat_params_effective", "sent": ["seed"], "not_sent": ["thinking"]}
    ]


def test_sampling_follows_the_thinking_that_is_actually_sent() -> None:
    """gpt-5.1+: Temperature only rides with Thinking Off (LangChain's validate_temperature)."""
    gpt51 = connection_wire(
        _connection("openai", {"thinking": "off", "temperature": 0.2}, "gpt-5.1")
    )
    assert dict(gpt51.first_class) == {"reasoning_effort": "none", "temperature": 0.2}
    low = connection_wire(_connection("openai", {"thinking": "low", "temperature": 0.2}, "gpt-5.1"))
    assert dict(low.first_class) == {"reasoning_effort": "low"}


def test_unhonoured_by_tables_flags_what_the_static_tables_hide() -> None:
    """§6 rule 3 input: the family table (gpt-5 has no none effort) and D5 (vLLM hides Off)."""
    off = _params(thinking="off")
    assert unhonoured_by_tables(off, ProviderProfile.OPENAI, "gpt-5-mini") == ("thinking",)
    assert unhonoured_by_tables(off, ProviderProfile.OPENROUTER, "openai/gpt-5-mini") == (
        "thinking",
    )
    assert unhonoured_by_tables(off, ProviderProfile.VLLM, "Qwen/Qwen3-8B") == ("thinking",)
    assert unhonoured_by_tables(off, ProviderProfile.GENERIC, "some-model") == ()
    temperature = _params(temperature=0.5)
    assert unhonoured_by_tables(temperature, ProviderProfile.OPENAI, "o3-mini") == ("temperature",)


def test_wire_summary_names_the_wire_fields_in_control_order() -> None:
    wire, _ = resolve_wire(_params(thinking="low", temperature=0.2, max_tokens=4096), _ALL_SHOWN)
    assert wire_summary(wire) == (
        "sent reasoning_effort=low, temperature=0.2, max_completion_tokens=4096"
    )
    assert wire_summary(NO_WIRE_PARAMS) == "sent nothing beyond the model"
    assert isinstance(NO_WIRE_PARAMS, WireParams) and NO_WIRE_PARAMS.chat_model_kwargs() == {}


def test_no_module_on_the_eval_wire_path_can_see_the_dialog_presets() -> None:
    """S7's structural isolation: the eval wire is sealed_arm_wire → resolve_wire →
    static_support → support_for → family_row. A card preset PRE-FILLS a widget; it must
    never become a silently-sent default, and that cannot rest on care alone."""
    from pathlib import Path

    harness = Path(chat_wire_module.__file__).parent
    on_the_wire_path = (
        "chat_wire.py",
        "control_support.py",
        "provider_profiles.py",
        "binding_sent_settings.py",
    )
    for name in on_the_wire_path:
        assert "family_presets" not in (harness / name).read_text(encoding="utf-8"), name
