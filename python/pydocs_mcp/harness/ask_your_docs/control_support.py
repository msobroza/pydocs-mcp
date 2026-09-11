"""Which model settings to show for (display profile, model, metadata) — model-params v2 §3.

Pure and network-free. The rule is MASK: a control the server or model cannot
honour is hidden, never disabled. Every source below may only REMOVE controls
or options from the generic baseline (all five Thinking options and every
field), so the same inputs always give the same :class:`ControlSupport`.

Owner rules: D5 — no vLLM profile offers Thinking Off (unverified end to end);
D6 — no OpenRouter LISTING hides Max output tokens (measured: it honours
``max_completion_tokens`` even when the listing reports only ``max_tokens``).
A 400 from the endpoint itself still hides a control everywhere (§3.3).

Example:
    >>> from pydocs_mcp.harness.ask_your_docs.provider_profiles import DisplayProfile, ProviderProfile
    >>> support_for(DisplayProfile(ProviderProfile.OPENAI), "gpt-5-mini").thinking_labels
    ('Auto', 'Low', 'Medium', 'High')
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

# The parser that FILLS ``learned`` lives in its own module; re-exported so the
# call sites import the whole support vocabulary from one place.
from pydocs_mcp.harness.ask_your_docs.param_rejections import rejected_control_from_error
from pydocs_mcp.harness.ask_your_docs.provider_profiles import (
    ALL_THINKING,
    DisplayProfile,
    ProviderProfile,
    SamplingRule,
    family_row,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ThinkingLevel

__all__ = [
    "ControlSupport",
    "family_support",
    "litellm_support",
    "openrouter_support",
    "rejected_control_from_error",
    "support_for",
]

_T = ThinkingLevel
_EFFORT_LEVELS = {"none": _T.OFF, "low": _T.LOW, "medium": _T.MEDIUM, "high": _T.HIGH}


@dataclass(frozen=True, slots=True)
class ControlSupport:
    """What the dialog renders; an empty ``thinking_options`` hides the Thinking control."""

    thinking_options: tuple[ThinkingLevel, ...] = ALL_THINKING
    show_temperature: bool = True
    show_max_tokens: bool = True
    show_top_p: bool = True
    show_seed: bool = True
    max_tokens_ceiling: int | None = None
    thinking_on_off: bool = False  # label MEDIUM as "On" (D8)
    sampling: SamplingRule = SamplingRule.ALWAYS

    @property
    def thinking_labels(self) -> tuple[str, ...]:
        on = self.thinking_on_off
        return tuple(
            "On" if on and t is _T.MEDIUM else t.value.title() for t in self.thinking_options
        )

    def temperature_shown(self, thinking: ThinkingLevel | None) -> bool:
        return self.show_temperature and _sampling_allows(self.sampling, thinking)

    def top_p_shown(self, thinking: ThinkingLevel | None) -> bool:
        return self.show_top_p and _sampling_allows(self.sampling, thinking)


def _sampling_allows(rule: SamplingRule, thinking: ThinkingLevel | None) -> bool:
    inactive = thinking in (None, _T.AUTO, _T.OFF)
    return {
        SamplingRule.ALWAYS: True,
        SamplingRule.THINKING_INACTIVE: inactive,
        SamplingRule.THINKING_OFF: thinking is _T.OFF,
        SamplingRule.NEVER: False,
    }[rule]


def _thinking(allowed: set[ThinkingLevel] | tuple[ThinkingLevel, ...]) -> tuple[ThinkingLevel, ...]:
    """Canonical order, Auto first; Auto alone is no choice at all, so the control hides."""
    options = tuple(t for t in ALL_THINKING if t is _T.AUTO or t in allowed)
    return options if len(options) > 1 else ()


def _efforts(listed: Any) -> set[ThinkingLevel]:
    return {_EFFORT_LEVELS[e] for e in listed if e in _EFFORT_LEVELS}


def openrouter_support(entry: Mapping[str, Any]) -> ControlSupport:
    """The listing entry decides; efforts are intersected, never coerced; Max output tokens stays (D6)."""
    listed = set(entry.get("supported_parameters") or ())
    raw_reasoning = entry.get("reasoning")
    reasoning: Mapping[str, Any] = raw_reasoning if isinstance(raw_reasoning, Mapping) else {}
    efforts = reasoning.get("supported_efforts")
    allowed = {_T.LOW, _T.MEDIUM, _T.HIGH} if efforts is None else _efforts(efforts) - {_T.OFF}
    if efforts is not None and "none" in efforts and not reasoning.get("mandatory"):
        allowed.add(_T.OFF)
    return ControlSupport(
        thinking_options=_thinking(allowed) if "reasoning_effort" in listed else (),
        show_temperature="temperature" in listed,
        show_top_p="top_p" in listed,
        show_seed="seed" in listed,
    )


def litellm_support(row: Mapping[str, Any]) -> ControlSupport:
    """``/model_group/info``: efforts None = unknown (all five), () = hide, a tuple = intersect."""
    params = row.get("supported_openai_params")

    def listed(*names: str) -> bool:
        return params is None or any(name in params for name in names)

    efforts = row.get("supported_reasoning_efforts")
    allowed = set(ALL_THINKING) if efforts is None else _efforts(efforts)
    anthropic = "anthropic" in (row.get("providers") or ())
    ceiling = row.get("max_output_tokens")
    return ControlSupport(
        thinking_options=_thinking(allowed) if listed("reasoning_effort") else (),
        show_temperature=listed("temperature"),
        show_max_tokens=listed("max_tokens", "max_completion_tokens"),
        show_top_p=listed("top_p"),
        show_seed=listed("seed"),
        max_tokens_ceiling=int(ceiling) if ceiling else None,
        sampling=SamplingRule.THINKING_INACTIVE if anthropic else SamplingRule.ALWAYS,
    )


def family_support(base: ControlSupport, profile: ProviderProfile, model: str) -> ControlSupport:
    """Intersect with the static family table, then apply D5 on vLLM."""
    row = family_row(model, profile)
    if row is not None:
        base = replace(
            base,
            thinking_options=_thinking(set(base.thinking_options) & set(row.thinking)),
            thinking_on_off=row.on_off,
            sampling=max(base.sampling, row.sampling),
        )
    if profile is ProviderProfile.VLLM:
        # D5: Off is hidden on vLLM until someone verifies reasoning_effort="none" end to end.
        base = replace(base, thinking_options=_thinking(set(base.thinking_options) - {_T.OFF}))
    return base


def _profile_support(
    display: DisplayProfile, entry: Mapping[str, Any] | None, row: Mapping[str, Any] | None
) -> ControlSupport:
    """The per-profile source; with nothing known a profile keeps the generic baseline."""
    profile = display.profile
    if profile is ProviderProfile.OPENROUTER and entry is not None:
        return openrouter_support(entry)
    if profile is ProviderProfile.LITELLM and row is not None:
        return litellm_support(row)
    if profile is ProviderProfile.VLLM:
        ceiling = (entry or {}).get("max_model_len")
        return ControlSupport(max_tokens_ceiling=ceiling if isinstance(ceiling, int) else None)
    return ControlSupport(show_max_tokens=not display.ignores_output_cap)


def _hide_learned(support: ControlSupport, learned: frozenset[str]) -> ControlSupport:
    """A 400 naming a sent param hides that control for the session, on every profile (§3.3).

    D6 lives one layer up: :func:`openrouter_support` never reads the cap off the listing,
    so OpenRouter keeps Max output tokens until the endpoint itself refuses the field.
    """
    return replace(
        support,
        thinking_options=() if "thinking" in learned else support.thinking_options,
        show_temperature=support.show_temperature and "temperature" not in learned,
        show_max_tokens=support.show_max_tokens and "max_tokens" not in learned,
        show_top_p=support.show_top_p and "top_p" not in learned,
        show_seed=support.show_seed and "seed" not in learned,
    )


def support_for(
    display: DisplayProfile,
    model: str,
    entry: Mapping[str, Any] | None = None,
    group_info: Mapping[str, Any] | None = None,
    learned: frozenset[str] = frozenset(),
) -> ControlSupport:
    """The controls to render for ``model``; ``learned`` holds this session's rejected controls."""
    base = _profile_support(display, entry, group_info)
    base = family_support(base, display.profile, model)
    return _hide_learned(base, learned)
