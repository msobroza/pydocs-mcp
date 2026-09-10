"""The Connection dialog's Test-connection round-trip (design §4.9 item 5, E11).

Moved out of ``llm_connection`` to keep that module inside its line budget;
``llm_connection`` re-exports :func:`run_connection_test`, so its import path is unchanged.
Light by contract: the chat factory (and so ``langchain_openai``) loads function-locally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerSource,
    redacted_failure_caption,
    translate_auth_errors,
)

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

_TEST_CONNECTION_TIMEOUT_SECONDS = 15.0
_TEST_CONNECTION_PROMPT = "Reply with the single word OK."
_TEST_REPLY_MAX_CHARS = 40
# The failure caption is endpoint-controlled text too, so it is bounded like the reply —
# wider, because a class name plus a redacted message needs the room. Unbounded, a chatty
# gateway's error body would flood the dialog line the reply is capped out of.
_TEST_FAILURE_MAX_CHARS = 300


async def run_connection_test(
    connection: LlmConnection, bearer: BearerSource, *, transport: Any = None
) -> str:
    """One round-trip on a candidate connection (design §4.9 item 5, E11) — always a caption.

    AC-43 is "always a caption, never a raise", so the CONSTRUCTION is inside the
    boundary too: a connection with no model chosen yet, or the no-block path with
    OPENAI_API_KEY unset, fails in ``ChatOpenAI.__init__`` before any request, and the
    dialog must show that as the same redacted caption a request failure gets.
    """
    # WHY function-local: llm_connection re-exports this function, so a top-level import of
    # its factory would be circular.
    from pydocs_mcp.harness.ask_your_docs.llm_connection import build_chat_model

    try:
        llm = build_chat_model(
            connection,
            bearer,
            timeout_seconds=_TEST_CONNECTION_TIMEOUT_SECONDS,
            max_retries=0,
            transport=transport,
        )
        with translate_auth_errors(bearer):
            reply = await llm.ainvoke(_TEST_CONNECTION_PROMPT)
    except Exception as exc:  # broad on purpose: every failure becomes the caption, redacted (H4)
        return f"test failed: {redacted_failure_caption(exc, bearer)[:_TEST_FAILURE_MAX_CHARS]}"
    return f"test passed: {str(reply.content).strip()[:_TEST_REPLY_MAX_CHARS]}"


__all__ = ("run_connection_test",)
