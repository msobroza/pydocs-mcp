"""Image-handling policy for the ask-your-docs agent — spec 2026-07-11-multimodal-image-agent §3.5, §3.9.

Split out of ``ask_your_docs_models`` for the same reason ``ImagesConfig`` was: that
module holds the whole ``ask_your_docs:`` block and has to stay inside its line budget
(``tests/harness/ask_your_docs/test_module_line_budgets.py``). Both classes are
re-exported from there, so every existing import path keeps working.

Example:
    >>> MultimodalConfig().text_only_fallback
    'reject'
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 2026-09-05: was vision_subagent. A multimodal main model answers and sees in
# one prompt; set vision_subagent back for a separate describe hop (design R6).
_DEFAULT_PREFERRED_ARCHITECTURE = "inline"


class MultimodalDetectionConfig(BaseModel):
    """The capability-detection ladder's per-rung toggles (spec §3.9).

    ``override`` wins; probes are opt-in — they cost a network call (3) or a real LLM call (4).
    """

    model_config = ConfigDict(extra="forbid")

    override: bool | None = Field(default=None)
    static_table: bool = Field(default=True)
    endpoint_probe: bool = Field(default=False)
    image_probe: bool = Field(default=False)


class MultimodalConfig(BaseModel):
    """Image-handling policy for the ask-your-docs agent."""

    model_config = ConfigDict(extra="forbid")

    # What "auto" builds on a vision-capable model (see the dated constant above).
    preferred_architecture: str = Field(default=_DEFAULT_PREFERRED_ARCHITECTURE)
    detection: MultimodalDetectionConfig = Field(default_factory=MultimodalDetectionConfig)
    # Text-only models + attached images: "reject" fails loudly with the fix in hand
    # (user-requested content must not silently degrade — the raising side of the Null
    # Object asymmetry); "describe" proceeds text-only with an explicit cannot-see note.
    text_only_fallback: Literal["reject", "describe"] = Field(default="reject")


__all__ = ("MultimodalConfig", "MultimodalDetectionConfig")
