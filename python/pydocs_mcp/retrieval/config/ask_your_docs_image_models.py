"""The ``ask_your_docs.images`` config sub-model (spec 2026-07-11-multimodal-image-agent §3.5).

Moved out of ``ask_your_docs_models.py`` to keep that module inside its line budget;
it re-exports :class:`ImagesConfig`, so ``ask_your_docs_models.ImagesConfig`` still works.
Defaults are duplicated in ``defaults/default_config.yaml`` on purpose — the YAML is the
user-visible knob (CLAUDE.md §Default values).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImagesConfig(BaseModel):
    """Per-turn image attachment limits + the session reinspect store size."""

    model_config = ConfigDict(extra="forbid")

    max_per_turn: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=5_000_000, ge=1)
    # How many recently-attached images the session keeps (bytes live OUTSIDE
    # conversation history) so reinspect_images can re-read earlier attachments against
    # a NEW question without re-paying vision tokens per turn. 0 disables retention.
    session_retention: int = Field(default=12, ge=0, le=50)
    # Necessity gating: each reinspect call is a full vision-model call, so a per-turn
    # budget stops a looping agent from burning them; repeated same-args calls are
    # memoized (free) and don't count. 0 disables the tool's vision path entirely.
    max_reinspect_per_turn: int = Field(default=2, ge=0, le=10)


__all__ = ("ImagesConfig",)
