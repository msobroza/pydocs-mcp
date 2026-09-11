"""Which OpenAI-compatible server is this? The wire and display profiles (model-params v2 §2).

Two profiles, kept apart on purpose:

- **Wire** (:func:`wire_profile`): pure and network-free — the declared
  ``ask_your_docs.llm.provider``, else the host. Eval and any future
  ``extra_body`` route read only this, so the app and eval send the same bytes
  for the same YAML.
- **Display** (:func:`display_profile`): the dialog's view, refined from the
  cached ``/models`` entry and a LiteLLM ``/model_group/info`` row. It only ever
  decides which controls are HIDDEN — detection never adds or re-routes.

Example:
    >>> wire_profile("auto", "https://openrouter.ai/api/v1")
    <ProviderProfile.OPENROUTER: 'openrouter'>
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ProviderName, ThinkingLevel
from pydocs_mcp.retrieval.llm_clients.reasoning_models import REASONING_MODEL_PREFIXES

# Bumped when the Thinking → wire mapping changes; recorded with eval arms (phase 2 = 2).
THINKING_MAP_VERSION = 1

_OPENROUTER_HOST = "openrouter.ai"
_OPENAI_HOST = "api.openai.com"
_VLLM_OWNER = "vllm"  # vLLM hard-codes owned_by; LiteLLM rewrites it (so a proxy reads as litellm)
# llama.cpp and Ollama read only max_tokens/n_predict, while LangChain always sends
# max_completion_tokens — so an output cap would be silently ignored (§0).
_CAP_IGNORING_OWNERS = frozenset({"llamacpp", "library"})


class ProviderProfile(StrEnum):
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    VLLM = "vllm"
    LITELLM = "litellm"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    """The dialog's profile; ``ignores_output_cap`` is the llama.cpp / Ollama generic flavour."""

    profile: ProviderProfile
    ignores_output_cap: bool = False


def wire_profile(declared: ProviderName, base_url: str | None) -> ProviderProfile:
    """Declared provider > host openrouter.ai > api.openai.com or null base_url > generic."""
    if declared != "auto":
        return ProviderProfile(declared)
    if base_url is None:
        return ProviderProfile.OPENAI  # the SDK's vendor default
    host = (urlsplit(base_url).hostname or "").lower()
    if host == _OPENROUTER_HOST:
        return ProviderProfile.OPENROUTER
    return ProviderProfile.OPENAI if host == _OPENAI_HOST else ProviderProfile.GENERIC


def display_profile(
    wire: ProviderProfile,
    listing_entry: Mapping[str, Any] | None,
    group_info: Mapping[str, Any] | None,
    *,
    declared: bool = False,
) -> DisplayProfile:
    """Refine an UNDECIDED (``auto`` → generic) wire profile from what the endpoint reported.

    ``declared`` says step 1 of §2 already decided: a declared ``provider`` is final, so
    ``provider: generic`` stays generic even on a server detection would have named.
    """
    if wire is not ProviderProfile.GENERIC:
        return DisplayProfile(wire)
    owner = (listing_entry or {}).get("owned_by")
    detected = None if declared else _detected_profile(owner, group_info)
    if detected is not None:
        return DisplayProfile(detected)
    return DisplayProfile(ProviderProfile.GENERIC, ignores_output_cap=owner in _CAP_IGNORING_OWNERS)


def _detected_profile(owner: Any, group_info: Mapping[str, Any] | None) -> ProviderProfile | None:
    """Steps 4-5: vLLM's hard-coded ``owned_by``, then a LiteLLM ``/model_group/info`` row."""
    if owner == _VLLM_OWNER:
        return ProviderProfile.VLLM
    return ProviderProfile.LITELLM if group_info is not None else None


class SamplingRule(IntEnum):
    """When Temperature / Top p may be sent; a higher value is stricter (combine with ``max``)."""

    ALWAYS = 0
    # Only while Thinking is Auto or Off: Anthropic refuses sampling together with thinking.
    THINKING_INACTIVE = 1
    # Only while Thinking is Off: LangChain's validate_temperature for gpt-5.1+.
    THINKING_OFF = 2
    NEVER = 3


_T = ThinkingLevel
ALL_THINKING: tuple[ThinkingLevel, ...] = (_T.AUTO, _T.OFF, _T.LOW, _T.MEDIUM, _T.HIGH)
_NO_OFF = (_T.AUTO, _T.LOW, _T.MEDIUM, _T.HIGH)


@dataclass(frozen=True, slots=True)
class FamilyRow:
    """What a model family honours; ``vllm_only`` rows describe chat templates, not the vendor."""

    thinking: tuple[ThinkingLevel, ...]
    sampling: SamplingRule = SamplingRule.ALWAYS
    on_off: bool = False  # the family only switches thinking on or off: "On" stores medium (D8)
    vllm_only: bool = False


# One source: the vendor rows are keyed by the shared reasoning prefixes. Matched on
# the model id's LAST path segment, longest prefix wins (``gpt-5.`` = gpt-5.1+).
FAMILY_TABLE: Mapping[str, FamilyRow] = MappingProxyType(
    {
        **{prefix: FamilyRow(_NO_OFF, SamplingRule.NEVER) for prefix in REASONING_MODEL_PREFIXES},
        "gpt-5.": FamilyRow(ALL_THINKING, SamplingRule.THINKING_OFF),
        "gpt-4o": FamilyRow(()),
        "gpt-4.1": FamilyRow(()),
        "qwen3": FamilyRow((_T.AUTO, _T.OFF, _T.MEDIUM), on_off=True, vllm_only=True),
        # Harmony answers 400 to reasoning_effort="none".
        "gpt-oss": FamilyRow(_NO_OFF, vllm_only=True),
    }
)


# Not a prefix row: "gpt-5-chat" and "gpt-5.1-chat" share no prefix, and a chat model is
# the OPPOSITE of the gpt-5 rows — it takes sampling and refuses reasoning_effort.
_GPT5_CHAT_ROW = FamilyRow(())


def _is_gpt5_chat(name: str) -> bool:
    """LangChain's own carve-out (``validate_temperature``: ``"chat" not in model_lower``)."""
    return name.startswith("gpt-5") and "chat" in name


def family_row(model: str, profile: ProviderProfile) -> FamilyRow | None:
    """The longest-prefix row for ``model``'s last path segment that applies on ``profile``."""
    name = model.lower().rsplit("/", 1)[-1]
    if _is_gpt5_chat(name):
        return _GPT5_CHAT_ROW
    matches = [
        prefix
        for prefix, row in FAMILY_TABLE.items()
        if name.startswith(prefix) and (profile is ProviderProfile.VLLM or not row.vllm_only)
    ]
    return FAMILY_TABLE[max(matches, key=len)] if matches else None
