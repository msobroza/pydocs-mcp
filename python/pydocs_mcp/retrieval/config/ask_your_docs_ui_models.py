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

from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The placeholders an editor link may use; ``path`` is required (a link that names no
# file opens nothing).
_EDITOR_LINK_FIELDS = frozenset({"root", "path", "start_line"})


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
    editor_link: str | None = Field(default=None)

    @field_validator("editor_link")
    @classmethod
    def _known_placeholders(cls, link: str | None) -> str | None:
        if link is None:
            return None
        names = {name for _, name, _, _ in Formatter().parse(link) if name is not None}
        if names - _EDITOR_LINK_FIELDS or "path" not in names:
            raise ValueError(
                f"ask_your_docs.ui.activity.editor_link: got {link!r}, expected a URL "
                "naming {path} and otherwise only {root} / {start_line}"
            )
        return link


class ReasoningUiConfig(BaseModel):
    """How the model's reasoning is captured and shown; never a request change."""

    model_config = ConfigDict(extra="forbid")

    capture: bool = Field(default=True)  # keep what the endpoint already returns
    display: Literal["collapsed", "expanded", "hidden"] = Field(default="collapsed")
    max_chars: int = Field(default=20_000, ge=200, le=200_000)  # per turn, head + tail
    # A bool, not the proposal's "off | on": YAML 1.1 (PyYAML) reads bare off / on as
    # booleans, so an enum of those words would reject the user's own YAML.
    think_tags: bool = Field(default=False)
    availability: bool | None = Field(default=None)  # None = learn it from answers


class AskYourDocsUiConfig(BaseModel):
    """The ``ask_your_docs.ui:`` block."""

    model_config = ConfigDict(extra="forbid")

    activity: ActivityUiConfig = Field(default_factory=ActivityUiConfig)
    reasoning: ReasoningUiConfig = Field(default_factory=ReasoningUiConfig)


__all__ = ("ActivityUiConfig", "AskYourDocsUiConfig", "ReasoningUiConfig")
