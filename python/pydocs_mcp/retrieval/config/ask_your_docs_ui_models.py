"""The ``ask_your_docs.ui`` config sub-models — the chat page's activity panel (PROPOSAL §5).

Display-only settings: none of them changes a request sent to the model or the MCP
server, so reasoning effort and friends stay out (they would belong under
``ask_your_docs.llm``). YAML-tunable per CLAUDE.md §MCP API surface vs YAML; the MCP
surface is untouched. Defaults are duplicated in ``defaults/default_config.yaml`` on
purpose — the YAML is the user-visible knob (CLAUDE.md §Default values). The poll
interval and repaint throttle are module constants of the view, not settings.

Example:
    >>> AskYourDocsUiConfig().reasoning.display
    'collapsed'
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActivityUiConfig(BaseModel):
    """The per-turn activity panel: what it shows, how much, and for how many turns."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True)  # False = the plain spinner + answer, as before
    live: bool = Field(default=True)  # False = trace built after the turn (same builder)
    technical_details: bool = Field(default=False)  # the session-only toggle's default
    collapse_when_done: bool = Field(default=True)  # failed / stopped turns stay expanded
    result_preview_chars: int = Field(default=600, ge=0, le=5000)
    args_max_chars: int = Field(default=2000, ge=40, le=20_000)
    max_steps_shown: int = Field(default=40, ge=1, le=500)  # then "+N more steps"
    history_keep: int = Field(default=20, ge=0, le=500)  # older turns: summary + sources
    # NOT here yet: the proposal's `editor_link` — it belongs to the citation-chip popover,
    # which is not built; extra="forbid" rejects it rather than ignore it silently.


class ReasoningUiConfig(BaseModel):
    """How the model's reasoning is captured and shown; never a request change."""

    model_config = ConfigDict(extra="forbid")

    # False = the stock chat model (reads no reasoning field); the request is the same.
    capture: bool = Field(default=True)
    display: Literal["collapsed", "expanded", "hidden"] = Field(default="collapsed")
    max_chars: int = Field(default=20_000, ge=200, le=200_000)  # per turn, head + tail
    # NOT here yet: the proposal's `think_tags` — reasoning_capture.ThinkTagSplitter exists
    # but no turn path runs it, so the setting would be a silent no-op.
    availability: bool | None = Field(default=None)  # None = learn it from answers


class AskYourDocsUiConfig(BaseModel):
    """The ``ask_your_docs.ui:`` block."""

    model_config = ConfigDict(extra="forbid")

    activity: ActivityUiConfig = Field(default_factory=ActivityUiConfig)
    reasoning: ReasoningUiConfig = Field(default_factory=ReasoningUiConfig)


__all__ = ("ActivityUiConfig", "AskYourDocsUiConfig", "ReasoningUiConfig")
