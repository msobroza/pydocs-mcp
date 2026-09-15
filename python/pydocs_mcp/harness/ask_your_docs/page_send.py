"""The chat page's send path: typed tokens, the pre-send refusals, the images, the entry.

Split out of ``app.py`` (its line budget): what happens between the composer's
submission and ``send_question`` — the ``in:`` / ``on:`` token parse (UI spec §6.10a),
the refusals that stop a question before any tool or model call (E13–E15, design E19,
the image policy of spec §3.8), the images the turn may carry, and the scope the
question is sent under — plus the transcript entry a send records first. The follow-up
chips reach only that last piece: they carry a canned question and a ready pin (§6.9),
so they never parse tokens.

Example:
    if submission:
        handle_submission(submission, listing=listing, ..., send_question=send_question)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Protocol

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.attachments import (
    AttachedSymbol,
    ImageAttachment,
    text_only_policy,
    update_image_store,
)
from pydocs_mcp.harness.ask_your_docs.page_turn import collect_images, refuse
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    scope_caption_text,
    snapshot_pin_for_send,
    token_scope,
)
from pydocs_mcp.harness.ask_your_docs.scope_tokens import ParsedScopeTokens, parse_scope_tokens
from pydocs_mcp.harness.ask_your_docs.transcript import user_transcript_entry

if TYPE_CHECKING:
    from streamlit.elements.widgets.chat import ChatInputValue

    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource
    from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
    from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
    from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
    from pydocs_mcp.harness.ask_your_docs.strip_state import StripState
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

CANNOT_SEE_IMAGES = "The model cannot see the attached image(s); answering from text only."


class QuestionSender(Protocol):
    """``app.send_question``'s shape: the ONE send path both the composer and the chips use.

    ``question`` is what the model gets (tokens stripped); ``display_question`` is what
    the transcript shows — the typed text, tokens and all; ``from_question`` marks the
    scope caption as typed (§6.10a). A chip passes neither."""

    def __call__(
        self,
        question: str,
        images: tuple[ImageAttachment, ...],
        scope: QuestionScope,
        transient_note: str = "",
        *,
        display_question: str = "",
        from_question: bool = False,
    ) -> None: ...


def handle_submission(
    submission: ChatInputValue,
    *,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    strip: StripState,
    active_scope: QuestionScope,
    config: AskYourDocsConfig,
    attached: Sequence[AttachedSymbol | str],
    vision_capabilities: ModelCapabilities | None,
    model: str | None,
    bearer: BearerSource,
    refuse_unless_connected: Callable[[str], None],
    send_question: QuestionSender,
) -> None:
    """One composer submission: parse the tokens, refuse, admit the images, pin, send.

    The token parse runs FIRST — before the connection and image refusals — so a
    mistyped ``in:`` name is answered by its own sentence and not by an unrelated
    one; every refusal quotes the TYPED text, tokens and all (§6.10a). A token
    question is a one-shot PIN over its cells: the strip is read and never written.
    """
    typed = submission.text or ""
    parsed = parse_typed_question(
        typed, listing=listing, capabilities=capabilities, strip=strip, config=config
    )
    if parsed.refusal:
        refuse(typed, parsed.refusal, bearer)
    refuse_unless_connected(typed)
    images = collect_images(list(submission.files or ()), config.images)
    images, transient_note = apply_text_only_policy(
        images, typed, config, vision_capabilities, model, bearer
    )
    scope = scope_for_send(parsed.cells, active_scope, attached, listing)
    send_question(
        parsed.stripped_text,
        images,
        scope,
        transient_note,
        display_question=typed,
        from_question=bool(parsed.cells),
    )


def parse_typed_question(
    typed: str,
    *,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    strip: StripState,
    config: AskYourDocsConfig,
) -> ParsedScopeTokens:
    """The typed text's tokens under the YAML switches; the strip lends its projects to
    the lone-``on:`` rule (§6.10a) and nothing here writes it back."""
    return parse_scope_tokens(
        typed,
        listing,
        capabilities,
        strip.projects(),
        tokens_enabled=config.scope.tokens_enabled,
        max_cells=config.scope.max_cells,
    )


def scope_for_send(
    token_cells: tuple[ScopeCell, ...],
    active_scope: QuestionScope,
    attached: Sequence[AttachedSymbol | str],
    listing: WorkspaceBranchListing,
) -> QuestionScope:
    """The scope THIS send goes under: the strip's, or a one-shot PIN over the token cells.

    The strip is sticky, so nothing is written back: a token question pins its own
    cells for one question (the strip's targets are not added, §6.10a), and either way
    the attached symbols' cells ride along (AC-30) — the woven text names them, so the
    tools must be able to reach them.
    """
    base = token_scope(token_cells, active_scope) if token_cells else active_scope
    return snapshot_pin_for_send(base, attached, listing)


def apply_text_only_policy(
    images: tuple[ImageAttachment, ...],
    typed: str,
    config: AskYourDocsConfig,
    vision_capabilities: ModelCapabilities | None,
    model: str | None,
    bearer: BearerSource,
) -> tuple[tuple[ImageAttachment, ...], str]:
    """The images this turn may carry, and the note ``ask()`` gets when it may not.

    The VISION half decides, never the main verdict: under a separate vision model the
    main model is blind by design while the images still have a reader. A rejection is
    a policy check, not an exception (spec §3.8), so it goes through ``refuse``.
    """
    verdict = text_only_policy(images, vision_capabilities, config.multimodal, model=model)
    if verdict is None:
        return images, ""
    if verdict.kind == "reject":
        refuse(typed, verdict.message, bearer)  # NoReturn: nothing is sent
    if verdict.kind != "describe":  # a kind this page has no policy for: keep the images
        return images, ""
    st.warning(CANNOT_SEE_IMAGES)
    # The cannot-see note rides ask()'s transient_note (attached AFTER reformulation,
    # never persisted) — the scope-pin pattern.
    return (), verdict.message


def image_chip_markdown(names: Iterable[str]) -> str:
    """The ``🖼 name`` pills a turn's images show as — ONE spelling for both views: the
    question this send records, and the "attached to the last question" row app.py draws."""
    return " ".join(f"`🖼 {name}`" for name in names)


def remember_images_for_reinspection(
    images: tuple[ImageAttachment, ...], *, retention: int
) -> dict[str, ImageAttachment]:
    """Fold this turn's images into the session store; returns the store as it was BEFORE.

    Bytes from recent turns stay reinspectable by the ``reinspect_images`` tool (history
    itself keeps only the placeholder). The snapshot is taken BEFORE the fold because this
    turn's attachment was just seen (inline) or extracted (vision node): only LATER
    questions need to reinspect it, and a same-turn re-read would be a wasted vision call
    (necessity gating).
    """
    image_store = st.session_state.setdefault("image_store", {})
    prior_images = dict(image_store)
    update_image_store(image_store, images, retention=retention)
    return prior_images


def record_question(
    shown_question: str,
    images: tuple[ImageAttachment, ...],
    scope: QuestionScope,
    from_question: bool,
    *,
    retention: int,
) -> dict[str, ImageAttachment]:
    """Show the question as TYPED (with its scope caption) and keep it; returns the PRIOR
    image store. ``from_question`` marks a caption whose cells came from typed tokens;
    ``retention`` is ``ask_your_docs.images.session_retention``."""
    st.session_state.image_chips = [att.name for att in images]
    prior_images = remember_images_for_reinspection(images, retention=retention)
    shown = shown_question + (
        "\n\n" + image_chip_markdown(att.name for att in images) if images else ""
    )
    caption = scope_caption_text(scope, from_question=from_question)
    st.session_state.messages.append(user_transcript_entry(shown, caption))
    with st.chat_message("user"):
        if caption:
            st.caption(caption)
        st.markdown(shown)
    return prior_images


__all__ = (
    "CANNOT_SEE_IMAGES",
    "QuestionSender",
    "apply_text_only_policy",
    "handle_submission",
    "image_chip_markdown",
    "parse_typed_question",
    "record_question",
    "remember_images_for_reinspection",
    "scope_for_send",
)
