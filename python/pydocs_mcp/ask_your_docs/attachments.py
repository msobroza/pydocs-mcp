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


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """The text-only degradation decision for a turn carrying images (§3.8)."""

    kind: str  # "reject" | "describe"
    message: str  # reject: the surfaced error; describe: the question prefix note


def text_only_policy(
    images: tuple[ImageAttachment, ...],
    capabilities: object,
    cfg: object,
    *,
    model: str,
) -> PolicyVerdict | None:
    """Decide the turn's fate when images meet a text-only model.

    Returns None when no policy applies (no images, or the model can see).
    Images are user-requested content — the raising side of the Null Object
    asymmetry — so ``reject`` (default) fails loudly with the fix in hand;
    ``describe`` proceeds text-only with an explicit cannot-see note.
    """
    if not images or getattr(capabilities, "multimodal", False):
        return None
    names = ", ".join(att.name for att in images)
    source = getattr(capabilities, "source", "default")
    if getattr(cfg, "text_only_fallback", "reject") == "describe":
        return PolicyVerdict(
            kind="describe",
            message=(
                f"[note: the user attached image(s) ({names}) that this model "
                "cannot see — say so explicitly in your answer and answer from "
                "text only]"
            ),
        )
    return PolicyVerdict(
        kind="reject",
        message=(
            f"The model {model!r} was detected as text-only (source={source}), "
            "so the attached image cannot be read. Remove the image, switch to "
            "a vision-capable model, or — if the detection is wrong — set "
            "ask_your_docs.multimodal.detection.override: true in your config."
        ),
    )


def update_image_store(
    store: dict[str, ImageAttachment],
    images: tuple[ImageAttachment, ...],
    *,
    retention: int,
) -> None:
    """Fold this turn's images into the session store, newest-last, evicting
    the oldest beyond ``retention``. Re-attaching a name refreshes its slot.

    The store is the reinspect_images tool's source: image BYTES live here —
    per session, outside conversation history, which keeps only the textual
    placeholder (the history non-goal is untouched).
    """
    if retention <= 0:
        return
    for att in images:
        store.pop(att.name, None)  # refresh position on re-attach
        store[att.name] = att
    while len(store) > retention:
        del store[next(iter(store))]  # dicts preserve insertion order


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
    "PolicyVerdict",
    "text_only_policy",
    "validate_attachment",
    "weave_attachments",
)
