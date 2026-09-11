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


def declared_numbers(entry: Mapping[str, Any] | None) -> Mapping[str, float]:
    """The listing's ``default_parameters`` that are real numbers (bool is an int subclass).

    THE rule for "this deployment already applies that value", and both consequences read
    it: the key becomes a placeholder here (a placeholder is never sent) and is netted out
    of the family preset, so the two can never disagree about a key.
    """
    listed = (entry or {}).get("default_parameters")
    known: Mapping[str, Any] = listed if isinstance(listed, Mapping) else {}
    return MappingProxyType(
        {
            name: value
            for name, value in known.items()
            if not isinstance(value, bool) and isinstance(value, int | float)
        }
    )


def settings_placeholders(
    entry: Mapping[str, Any] | None, support: ControlSupport
) -> dict[str, str]:
    """Known defaults only (OpenRouter ``default_parameters``, the output ceiling)."""
    known = declared_numbers(entry)
    shown = {name: _placeholder(known.get(name)) for name in CONTROL_LABELS if name != "thinking"}
    if support.max_tokens_ceiling:
        shown["max_tokens"] = f"{MODEL_DEFAULT} (max {support.max_tokens_ceiling})"
    return shown


def _placeholder(value: float | None) -> str:
    return MODEL_DEFAULT if value is None else f"{value:g}"


__all__ = (
    "CONTROL_LABELS",
    "MODEL_DEFAULT",
    "SettingsView",
    "declared_numbers",
    "provider_word",
    "settings_placeholders",
)
