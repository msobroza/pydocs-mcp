"""The Connection dialog's masked model-settings section (model-params v2 §3.1, §5).

Rendered below the model picker: Thinking, Temperature and Max output tokens, then a
collapsed **More** with Top p, Seed and the two buttons. MASK: a control or option the
endpoint cannot honour is not rendered at all — no disabled row, no caption — and its saved
value rides through the returned snapshot untouched; whether it is SENT is
``resolve_wire``'s decision (D7). Imported by the page path alone, so ``streamlit`` stays
out of ``cli.py``; the streamlit-free half (labels, placeholders, the view) is
``settings_view``.

Example:
    params = render_model_settings(view, connection.params, candidate.params, restore)
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
from pydocs_mcp.harness.ask_your_docs.settings_view import (
    CONTROL_LABELS,
    MODEL_DEFAULT,
    SettingsView,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import (
    ChatParamsConfig,
    ThinkingLevel,
    param_bounds,
)

# Widget keys (the mockup's) — AppTest addresses widgets by key.
KEY_THINKING = "connection_param_thinking"
KEY_TEMPERATURE = "connection_param_temperature"
KEY_MAX_TOKENS = "connection_param_max_tokens"
KEY_TOP_P = "connection_param_top_p"
KEY_SEED = "connection_param_seed"
KEY_USE_YAML = "connection_param_use_yaml"
KEY_RESTORE = "connection_param_restore"
# Session state: the set the widgets restart from after "Use YAML settings"; Apply drops it.
STATE_PARAMS_BASE = "connection_params_base"

_KEYS = {
    "thinking": KEY_THINKING,
    "temperature": KEY_TEMPERATURE,
    "max_tokens": KEY_MAX_TOKENS,
    "top_p": KEY_TOP_P,
    "seed": KEY_SEED,
}
_FLOAT_STEP = 0.05
_FLOAT_FORMAT = "%.2f"
# number_input refuses bounds past JavaScript's safe integers (streamlit's JSNumber check).
_JS_MAX_SAFE_INTEGER = 2**53 - 1
# number_input's minimum is inclusive, so top_p's (0, 1] is floored at the smallest value
# its two-decimal format can show.
_EXCLUSIVE_FLOOR = 0.01


def render_model_settings(
    view: SettingsView,
    snapshot: ChatParamsConfig,
    yaml_params: ChatParamsConfig,
    restore: Callable[[], None],
) -> ChatParamsConfig:
    """The section; returns the COMPLETE snapshot (a hidden control keeps its saved value).

    ``snapshot`` pre-fills (the page's effective set), ``yaml_params`` is what "Use YAML
    settings" puts back, ``restore`` forgets this model's learned rejections.
    """
    base = st.session_state.get(STATE_PARAMS_BASE, snapshot)
    support = view.support
    thinking = _render_thinking(support, base)
    # Sampling follows the Thinking that is SENT — the same rule resolve_wire applies.
    sent = thinking if thinking in support.thinking_options else None
    values = {
        "thinking": thinking,
        "temperature": _field(view, base, "temperature", support.temperature_shown(sent)),
        "max_tokens": _field(view, base, "max_tokens", support.show_max_tokens),
    }
    more = {"top_p": support.top_p_shown(sent), "seed": _seed_shown(support, base)}
    use_yaml = functools.partial(_use_yaml, yaml_params)
    values.update(_render_more(view, base, more, use_yaml, restore))
    return ChatParamsConfig(**values)


def _render_thinking(support: ControlSupport, base: ChatParamsConfig) -> ThinkingLevel | None:
    """The segmented control; no selection keeps a saved value the endpoint hides (D5, D7)."""
    if not support.thinking_options:
        return base.thinking
    levels = dict(zip(support.thinking_labels, support.thinking_options, strict=True))
    saved = base.thinking or ThinkingLevel.AUTO
    default = next((label for label, level in levels.items() if level is saved), None)
    label = st.segmented_control(
        CONTROL_LABELS["thinking"], list(levels), default=default, key=KEY_THINKING
    )
    if label is None:
        return None if saved in support.thinking_options else base.thinking
    return None if levels[label] is ThinkingLevel.AUTO else levels[label]  # On stores medium


def _field(
    view: SettingsView, base: ChatParamsConfig, name: str, shown: bool, where: Any = st
) -> float | int | None:
    """A shown control's widget value (blank = None); a hidden one's saved value, untouched."""
    saved = getattr(base, name)
    if not shown:
        return saved
    integer = param_bounds(name).integer
    # WHY session state, not value=: number_input answers a CLEARED field with its value=
    # default (NumberInputSerde.deserialize), so a pre-filled default could never be blanked.
    # With value=None a clear reads as None ("not sent"); the pre-fill lands only once.
    if _KEYS[name] not in st.session_state:
        st.session_state[_KEYS[name]] = saved if saved is None or integer else float(saved)
    return where.number_input(
        CONTROL_LABELS[name],
        value=None,
        key=_KEYS[name],
        placeholder=view.placeholders.get(name, MODEL_DEFAULT),
        **(_integer_limits(name, view.support, saved) if integer else _float_limits(name)),
    )


def _float_limits(name: str) -> dict[str, Any]:
    """The widget's range, read off the config ``Field`` (``param_bounds``) — never re-spelled."""
    bounds = param_bounds(name)
    floor = bounds.minimum + (_EXCLUSIVE_FLOOR if bounds.exclusive_minimum else 0.0)
    ceiling = None if bounds.maximum is None else float(bounds.maximum)
    return {
        "min_value": float(floor),
        "max_value": ceiling,
        "step": _FLOAT_STEP,
        "format": _FLOAT_FORMAT,
    }


def _integer_limits(name: str, support: ControlSupport, saved: int | None) -> dict[str, Any]:
    ceiling = support.max_tokens_ceiling if name == "max_tokens" else param_bounds(name).maximum
    if ceiling is not None:
        # A saved value above a reported ceiling must not crash the dialog; the server answers it.
        ceiling = min(max(int(ceiling), saved or 0), _JS_MAX_SAFE_INTEGER)
    return {"min_value": int(param_bounds(name).minimum), "max_value": ceiling, "step": 1}


def _seed_shown(support: ControlSupport, base: ChatParamsConfig) -> bool:
    # A seed past JavaScript's safe integers cannot sit in a number_input: it rides as saved.
    return support.show_seed and (base.seed is None or base.seed <= _JS_MAX_SAFE_INTEGER)


def _render_more(
    view: SettingsView,
    base: ChatParamsConfig,
    shown: Mapping[str, bool],
    use_yaml: Callable[[], None],
    restore: Callable[[], None],
) -> dict[str, Any]:
    """Top p and Seed under a collapsed More, with the buttons; absent when it would be empty."""
    carried = {name: getattr(base, name) for name in shown}
    if not any(shown.values()) and not view.hidden_count:
        return carried
    with st.expander("More"):
        for name, column in zip(shown, st.columns(len(shown)), strict=True):
            carried[name] = _field(view, base, name, shown[name], column)
        left, right = st.columns(2)
        left.button("Use YAML settings", key=KEY_USE_YAML, on_click=use_yaml)
        if view.hidden_count:
            label = f"Restore hidden settings ({view.hidden_count})"
            right.button(label, key=KEY_RESTORE, on_click=restore)
    return carried


def _use_yaml(yaml_params: ChatParamsConfig) -> None:
    """A button callback (runs before the rerun): every widget restarts from the YAML set."""
    for key in _KEYS.values():
        st.session_state.pop(key, None)
    st.session_state[STATE_PARAMS_BASE] = yaml_params


__all__ = (
    "KEY_MAX_TOKENS",
    "KEY_RESTORE",
    "KEY_SEED",
    "KEY_TEMPERATURE",
    "KEY_THINKING",
    "KEY_TOP_P",
    "KEY_USE_YAML",
    "STATE_PARAMS_BASE",
    "render_model_settings",
)
