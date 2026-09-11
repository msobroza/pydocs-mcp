"""What the Connection dialog may offer for one (endpoint, model) — model-params v2 §3.1, §5.

The streamlit-free half of the model-settings section: the control labels, the provider
word that ends the dialog's status line, the placeholders (known defaults only — a
placeholder is never sent) and :class:`SettingsView`, the page's verdict the form renders.

Example:
    >>> provider_word(ProviderProfile.VLLM)
    'vLLM'
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
from pydocs_mcp.harness.ask_your_docs.family_presets import ThinkingPreset
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile

# One vocabulary for the widgets, the placeholders and the state-G chat message.
CONTROL_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "thinking": "Thinking",
        "temperature": "Temperature",
        "max_tokens": "Max output tokens",
        "top_p": "Top p",
        "seed": "Seed",
    }
)
MODEL_DEFAULT = "model default"
_PROVIDER_WORDS: Mapping[ProviderProfile, str] = MappingProxyType(
    {
        ProviderProfile.OPENAI: "OpenAI",
        ProviderProfile.OPENROUTER: "OpenRouter",
        ProviderProfile.VLLM: "vLLM",
        ProviderProfile.LITELLM: "LiteLLM",
        ProviderProfile.GENERIC: "provider unknown — settings unverified",
    }
)


@dataclass(frozen=True, slots=True)
class SettingsView:
    """What the page decided the dialog may offer for one (endpoint, model)."""

    profile: ProviderProfile
    support: ControlSupport
    placeholders: Mapping[str, str] = field(default_factory=dict)
    hidden_count: int = 0  # this session's learned rejections: "Restore hidden settings (N)"
    # The family's card values, already netted against the listing's declared defaults.
    # It PRE-FILLS the dialog (so Test sends what Apply stores); it is never sent by itself.
    preset: ThinkingPreset | None = None


def provider_word(profile: ProviderProfile) -> str:
    """The dialog status line's last cell: the provider, or that nothing about it is known."""
    return _PROVIDER_WORDS[profile]


def settings_placeholders(
    entry: Mapping[str, Any] | None, support: ControlSupport
) -> dict[str, str]:
    """Known defaults only (OpenRouter ``default_parameters``, the output ceiling)."""
    listed = (entry or {}).get("default_parameters")
    known = listed if isinstance(listed, Mapping) else {}
    shown = {name: _placeholder(known.get(name)) for name in CONTROL_LABELS if name != "thinking"}
    if support.max_tokens_ceiling:
        shown["max_tokens"] = f"{MODEL_DEFAULT} (max {support.max_tokens_ceiling})"
    return shown


def _placeholder(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return MODEL_DEFAULT
    return f"{value:g}"


__all__ = (
    "CONTROL_LABELS",
    "MODEL_DEFAULT",
    "SettingsView",
    "provider_word",
    "settings_placeholders",
)
