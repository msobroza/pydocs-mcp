"""A model family's RECOMMENDED sampling values — the Connection dialog's pre-fill (S7).

Kept out of :mod:`provider_profiles` on purpose. A :class:`FamilyRow` is read by
``family_support`` → ``support_for`` → ``static_support`` → ``resolve_wire`` →
``sealed_arm_wire``, so a number parked there would sit one attribute access from
the EVAL wire. These values only ever pre-fill a widget: the dialog shows them, so
Test sends them and Apply stores them, and an arm that configures nothing keeps a
byte-identical fingerprint. Nothing on the wire path imports this module — pinned
by a source assertion in ``test_chat_wire``.

Example:
    >>> dict(family_preset("qwen/qwen3.8-27b").values(thinking_off=False))
    {'temperature': 1.0, 'top_p': 0.95}
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from pydocs_mcp.harness.ask_your_docs.provider_profiles import last_segment, longest_prefix_key


@dataclass(frozen=True, slots=True)
class ThinkingPreset:
    """One family's card values: the thinking mode, the non-thinking mode, and the D4 rest."""

    on: Mapping[str, float]
    off: Mapping[str, float]
    # top_k / min_p / presence_penalty / repetition_penalty as (thinking, non-thinking).
    # Phase 1 has no route for them (ChatParamsConfig refuses every one); recorded so the
    # D4 extra_body PR can pick them up. No code path sends them.
    not_yet_routable: Mapping[str, tuple[float, float]]

    def values(self, *, thinking_off: bool) -> Mapping[str, float]:
        """The branch the dialog is on — the same predicate ``resolve_wire`` calls ``thinking_off``."""
        return self.off if thinking_off else self.on


# WHY these numbers: the Qwen3.8-27B model card's recommended generation parameters,
# https://huggingface.co/Qwen/Qwen3.8-27B — thinking mode is the owner's standard
# (2026-09-11), and turning Thinking off in the dialog swaps to the instruct row. The card
# states output CEILINGS, not a default, so max_tokens is deliberately absent. Matched by
# the same longest-prefix-on-the-last-path-segment rule as FAMILY_TABLE: one place to edit
# for the next Qwen generation.
PRESET_TABLE: Mapping[str, ThinkingPreset] = MappingProxyType(
    {
        "qwen3.8": ThinkingPreset(
            on=MappingProxyType({"temperature": 1.0, "top_p": 0.95}),
            off=MappingProxyType({"temperature": 0.7, "top_p": 0.80}),
            not_yet_routable=MappingProxyType(
                {
                    "top_k": (20, 20),
                    "min_p": (0.0, 0.0),
                    "presence_penalty": (0.0, 1.5),
                    "repetition_penalty": (1.0, 1.0),
                }
            ),
        )
    }
)


def family_preset(model: str) -> ThinkingPreset | None:
    """The card row for ``model``, or None when no family the dialog knows recommends values."""
    key = longest_prefix_key(last_segment(model), PRESET_TABLE)
    return None if key is None else PRESET_TABLE[key]


def preset_for(model: str, declared: Mapping[str, float]) -> ThinkingPreset | None:
    """The card row with the endpoint's own declared defaults (``declared_numbers``) netted out.

    Tier 2 of the pre-fill order beats tier 3: a number the endpoint already declares is
    applied by that deployment, so it stays a PLACEHOLDER (never sent) instead of becoming
    a pre-fill that would silently start sending it.
    """
    preset = family_preset(model)
    if preset is None or not declared:
        return preset
    return replace(preset, on=_without(preset.on, declared), off=_without(preset.off, declared))


def _without(values: Mapping[str, float], drop: Mapping[str, float]) -> Mapping[str, float]:
    return MappingProxyType({name: v for name, v in values.items() if name not in drop})


__all__ = ("PRESET_TABLE", "ThinkingPreset", "family_preset", "preset_for")
