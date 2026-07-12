"""Attachment value objects + weaving helpers for the ask-your-docs agent.

Light module by contract: NO heavy imports (streamlit / langgraph /
langchain) — it is imported by tests and by ``agent.py`` alike, and the
subpackage's lazy-import guarantee (``__init__.py`` PEP 562 block) must hold.

Spec: docs/superpowers/specs/2026-07-11-multimodal-image-agent-spec.md §3.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config.ask_your_docs_models import ImagesConfig

# Single source of truth for the shipped limits (the YAML defaults duplicate
# them intentionally — CLAUDE.md §Default values).
_MAX_IMAGE_BYTES_DEFAULT = 5_000_000
_MAX_IMAGES_PER_TURN_DEFAULT = 3
_ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """One user-attached image for the CURRENT question (transient, like the
    scope pin — never persisted into conversation history)."""

    name: str  # original filename, for chips + placeholders
    media_type: str  # one of _ALLOWED_IMAGE_TYPES
    data_b64: str  # base64 payload (no data: prefix)

    def as_content_block(self) -> dict:
        """OpenAI-compatible image_url content block (data URI)."""
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self.media_type};base64,{self.data_b64}"},
        }


def validate_attachment(att: ImageAttachment, cfg: ImagesConfig) -> None:
    """Reject a disallowed media type or an over-limit payload, naming the
    offending value and the limit (error-message convention)."""
    if att.media_type not in _ALLOWED_IMAGE_TYPES:
        raise ValueError(
            f"attachment {att.name!r} has media type {att.media_type!r}; "
            f"allowed: {', '.join(_ALLOWED_IMAGE_TYPES)}"
        )
    # base64 inflates by 4/3 — compare decoded size against the byte limit.
    approx_bytes = len(att.data_b64) * 3 // 4
    if approx_bytes > cfg.max_bytes:
        raise ValueError(
            f"attachment {att.name!r} is ~{approx_bytes} bytes decoded; "
            f"the configured limit is images.max_bytes={cfg.max_bytes}"
        )


def weave_attachments(attached: list[str], question: str) -> str:
    """Prepend de-duped attached symbols to a question as plain context text."""
    seen: dict[str, None] = {}
    for a in attached:
        if a:
            seen.setdefault(a, None)
    if not seen:
        return question
    names = ", ".join(f"`{a}`" for a in seen)
    return f"Regarding {names}: {question}"


__all__ = (
    "ImageAttachment",
    "validate_attachment",
    "weave_attachments",
)
