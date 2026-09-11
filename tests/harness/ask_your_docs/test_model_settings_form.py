"""``render_model_settings`` on its own, through ``AppTest.from_function`` (model-params v2 §5).

The form is handed a ``SettingsView`` (what the endpoint honours), the effective snapshot,
the YAML set and the Restore callback; it returns the COMPLETE snapshot the dialog stores.
Hidden controls are absent (MASK) and carry their saved value through untouched — whether
that value is sent is ``resolve_wire``'s decision (D7), never the form's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
from pydocs_mcp.harness.ask_your_docs.family_presets import PRESET_TABLE, ThinkingPreset
from pydocs_mcp.harness.ask_your_docs.model_settings_form import (
    KEY_MAX_TOKENS,
    KEY_RESTORE,
    KEY_SEED,
    KEY_TEMPERATURE,
    KEY_THINKING,
    KEY_TOP_P,
    KEY_USE_YAML,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile, SamplingRule
from pydocs_mcp.harness.ask_your_docs.settings_view import SettingsView
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import (
    ChatParamsConfig,
    ThinkingLevel,
)

_T = ThinkingLevel
_FULL = ChatParamsConfig(thinking="low", temperature=0.2, max_tokens=4096, top_p=0.9, seed=7)
_NO_PARAMS = ChatParamsConfig()
_BASELINE = ControlSupport()  # the generic endpoint: every control, every option
_ON_OFF = ControlSupport(thinking_options=(_T.AUTO, _T.OFF, _T.MEDIUM), thinking_on_off=True)
_QWEN38 = PRESET_TABLE["qwen3.8"]  # the shipped card values, not a fixture of their own


class FakeRestore:
    """The dialog's "Restore hidden settings" callback; counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def _form_page() -> None:
    import streamlit as st

    from pydocs_mcp.harness.ask_your_docs.model_settings_form import render_model_settings

    st.session_state["form_result"] = render_model_settings(*st.session_state["form_inputs"])


def _form(
    support: ControlSupport = _BASELINE,
    snapshot: ChatParamsConfig = _FULL,
    *,
    yaml_params: ChatParamsConfig = _NO_PARAMS,
    hidden: int = 0,
    restore: FakeRestore | None = None,
    preset: ThinkingPreset | None = None,
) -> AppTest:
    at = AppTest.from_function(_form_page, default_timeout=60)
    view = SettingsView(ProviderProfile.GENERIC, support, hidden_count=hidden, preset=preset)
    at.session_state["form_inputs"] = (view, snapshot, yaml_params, restore or FakeRestore())
    return _run(at)


def _run(target) -> AppTest:
    """Run the page — ``target`` is the AppTest or a widget that was just set or clicked."""
    at = target.run()
    assert not at.exception, at.exception
    return at


def _number_keys(at: AppTest) -> list[str]:
    return [field.key for field in at.number_input]


def _more_shown(at: AppTest) -> bool:
    return any(expander.label == "More" for expander in at.expander)


def test_the_snapshot_round_trips() -> None:
    at = _form()
    assert at.session_state["form_result"] == _FULL
    assert at.segmented_control(key=KEY_THINKING).value == "Low"
    assert _number_keys(at) == [KEY_TEMPERATURE, KEY_MAX_TOKENS, KEY_TOP_P, KEY_SEED]


def test_a_blank_field_gives_none() -> None:
    at = _form()
    _run(at.number_input(key=KEY_TEMPERATURE).set_value(None))
    assert at.session_state["form_result"] == _FULL.model_copy(update={"temperature": None})


def test_on_is_a_label_that_stores_medium() -> None:
    at = _form(_ON_OFF, ChatParamsConfig())
    assert list(at.segmented_control(key=KEY_THINKING).options) == ["Auto", "Off", "On"]
    _run(at.segmented_control(key=KEY_THINKING).set_value("On"))
    assert at.session_state["form_result"].thinking is _T.MEDIUM


def test_auto_is_stored_as_not_sent() -> None:
    at = _form()
    _run(at.segmented_control(key=KEY_THINKING).set_value("Auto"))
    assert at.session_state["form_result"].thinking is None


def test_a_hidden_control_is_absent_and_carries_its_saved_value() -> None:
    at = _form(ControlSupport(show_temperature=False, thinking_options=()))
    assert KEY_TEMPERATURE not in _number_keys(at)
    assert not [group for group in at.segmented_control if group.key == KEY_THINKING]
    assert at.session_state["form_result"] == _FULL  # D7: resolve_wire decides what is sent


def test_a_hidden_thinking_option_selects_nothing_and_is_kept() -> None:
    at = _form(
        ControlSupport(thinking_options=(_T.AUTO, _T.MEDIUM), thinking_on_off=True),
        ChatParamsConfig(thinking="off"),
    )
    assert at.segmented_control(key=KEY_THINKING).value is None
    assert at.session_state["form_result"].thinking is _T.OFF


def test_temperature_follows_the_thinking_choice() -> None:
    at = _form(
        ControlSupport(sampling=SamplingRule.THINKING_OFF), ChatParamsConfig(temperature=0.3)
    )
    assert KEY_TEMPERATURE not in _number_keys(at) and KEY_TOP_P not in _number_keys(at)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert at.number_input(key=KEY_TEMPERATURE).value == 0.3
    assert KEY_TOP_P in _number_keys(at)
    assert at.session_state["form_result"] == ChatParamsConfig(thinking="off", temperature=0.3)


def test_more_is_absent_when_it_would_be_empty() -> None:
    at = _form(ControlSupport(show_top_p=False, show_seed=False))
    assert not _more_shown(at)
    assert not [button for button in at.button if button.key == KEY_USE_YAML]


def test_restore_appears_only_after_a_learned_rejection() -> None:
    restore = FakeRestore()
    at = _form(ControlSupport(show_top_p=False, show_seed=False), hidden=1, restore=restore)
    assert _more_shown(at)
    assert at.button(key=KEY_RESTORE).label == "Restore hidden settings (1)"
    _run(at.button(key=KEY_RESTORE).click())
    assert restore.calls == 1
    assert not [button for button in _form().button if button.key == KEY_RESTORE]


def test_use_yaml_settings_puts_the_yaml_set_back() -> None:
    yaml_params = ChatParamsConfig(temperature=0.7)
    at = _form(yaml_params=yaml_params)
    _run(at.button(key=KEY_USE_YAML).click())
    assert at.session_state["form_result"] == yaml_params
    assert at.number_input(key=KEY_TEMPERATURE).value == 0.7
    assert at.segmented_control(key=KEY_THINKING).value == "Auto"


def test_the_widgets_read_their_bounds_from_the_config_fields() -> None:
    at = _form(ControlSupport(max_tokens_ceiling=40960))
    temperature = at.number_input(key=KEY_TEMPERATURE).proto
    assert (temperature.min, temperature.max) == (0.0, 2.0)
    assert at.number_input(key=KEY_MAX_TOKENS).proto.max == 40960
    assert at.number_input(key=KEY_TOP_P).proto.max == 1.0


# ── the family preset (S7): the card's values PRE-FILL, so Test sends what Apply stores ──


def _sampling(at: AppTest) -> tuple[float | None, float | None]:
    return at.number_input(key=KEY_TEMPERATURE).value, at.number_input(key=KEY_TOP_P).value


def test_a_card_preset_pre_fills_the_thinking_values() -> None:
    at = _form(snapshot=_NO_PARAMS, preset=_QWEN38)
    assert _sampling(at) == (1.0, 0.95)
    result = at.session_state["form_result"]
    assert (result.temperature, result.top_p) == (1.0, 0.95)


def test_turning_thinking_off_swaps_to_the_non_thinking_preset() -> None:
    at = _form(snapshot=_NO_PARAMS, preset=_QWEN38)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert _sampling(at) == (0.7, 0.80)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Auto"))
    assert _sampling(at) == (1.0, 0.95)


def test_a_typed_value_and_a_blanked_field_survive_a_thinking_flip() -> None:
    at = _form(snapshot=_NO_PARAMS, preset=_QWEN38)
    _run(at.number_input(key=KEY_TEMPERATURE).set_value(0.3))
    _run(at.number_input(key=KEY_TOP_P).set_value(None))
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert _sampling(at) == (0.3, None)


def test_a_saved_value_beats_the_card_on_every_branch() -> None:
    at = _form(snapshot=ChatParamsConfig(temperature=0.2), preset=_QWEN38)
    assert _sampling(at) == (0.2, 0.95)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert _sampling(at) == (0.2, 0.80)


def test_use_yaml_settings_ends_the_preset_for_the_session() -> None:
    at = _form(snapshot=_NO_PARAMS, preset=_QWEN38)
    _run(at.button(key=KEY_USE_YAML).click())
    assert _sampling(at) == (None, None)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert _sampling(at) == (None, None)
    assert at.session_state["form_result"] == ChatParamsConfig(thinking="off")


def test_a_model_without_a_preset_renders_exactly_as_before() -> None:
    at = _form(snapshot=_NO_PARAMS)
    assert _sampling(at) == (None, None)
    _run(at.segmented_control(key=KEY_THINKING).set_value("Off"))
    assert _sampling(at) == (None, None)
