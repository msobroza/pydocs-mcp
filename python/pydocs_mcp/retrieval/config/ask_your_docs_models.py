"""Ask-your-docs agent config sub-models (spec 2026-07-11-multimodal-image-agent §3.5).

The first agent-side consumer of AppConfig — sanctioned because agent
architecture choice and multimodal-detection strategy are "A/B-testable
against a benchmark" behaviors (CLAUDE.md §MCP API surface vs YAML
configuration litmus test). Light pydantic only: importing this from the
``[ask-your-docs]`` extra pulls no heavy deps.

Defaults are duplicated in ``defaults/default_config.yaml`` intentionally —
the YAML is the user-visible knob (CLAUDE.md §Default values).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MultimodalDetectionConfig(BaseModel):
    """The capability-detection ladder's per-rung toggles (spec §3.9).

    ``override`` always wins; the probes are opt-in because rung 3 adds a
    network call at agent build and rung 4 spends a real (tiny) LLM call.
    """

    model_config = ConfigDict(extra="forbid")

    override: bool | None = Field(default=None)
    static_table: bool = Field(default=True)
    endpoint_probe: bool = Field(default=False)
    image_probe: bool = Field(default=False)


class MultimodalConfig(BaseModel):
    """Image-handling policy for the ask-your-docs agent."""

    model_config = ConfigDict(extra="forbid")

    # What "auto" builds on a vision-capable model. vision_subagent is the
    # default: image tokens are paid once per turn, not per ReAct iteration.
    preferred_architecture: str = Field(default="vision_subagent")
    detection: MultimodalDetectionConfig = Field(default_factory=MultimodalDetectionConfig)
    # Text-only models + attached images: "reject" fails loudly with the fix
    # in hand (user-requested content must not silently degrade — the raising
    # side of the Null Object asymmetry); "describe" proceeds text-only with
    # an explicit cannot-see note.
    text_only_fallback: Literal["reject", "describe"] = Field(default="reject")


class ImagesConfig(BaseModel):
    """Per-turn image attachment limits."""

    model_config = ConfigDict(extra="forbid")

    max_per_turn: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=5_000_000, ge=1)


class AskYourDocsConfig(BaseModel):
    """Top-level ``ask_your_docs:`` block — agent architecture + multimodal policy."""

    model_config = ConfigDict(extra="forbid")

    # One of agent_registry.names(); "text_react" pins pre-image behavior
    # exactly, "auto" routes by the detected capability.
    architecture: str = Field(default="auto")
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)


__all__ = (
    "AskYourDocsConfig",
    "ImagesConfig",
    "MultimodalConfig",
    "MultimodalDetectionConfig",
)
