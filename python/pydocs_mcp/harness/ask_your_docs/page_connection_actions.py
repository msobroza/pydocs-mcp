"""The page side of the Connection dialog's ``ConnectionActions`` (design §4.9).

Moved out of ``app`` to keep that module inside its line budget. ``app`` keeps the
event loop and the cached functions these callbacks run through and injects them as
:class:`PageConnectionHooks`, so this module owns no Streamlit cache of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    redact_bearer,
    redacted_failure_caption,
)
from pydocs_mcp.harness.ask_your_docs.connection_dialog import NOTHING_RENEWED, ConnectionActions
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    LlmConnection,
    run_connection_test,
)
from pydocs_mcp.harness.ask_your_docs.model_listing import (
    ModelListing,
    cached_model_listing,
    clear_model_listing_cache,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import ListModels

if TYPE_CHECKING:  # the Test-connection seam's type only — the page never imports httpx at runtime
    import httpx

_T = TypeVar("_T")


class RunOnPageLoop(Protocol):
    """``app.run``: drive one coroutine on the page's single event loop, blocking for the result."""

    def __call__(self, coro: Coroutine[Any, Any, _T]) -> _T: ...


@dataclass(frozen=True, slots=True)
class PageConnectionHooks:
    """The page machinery the actions need; ``app`` owns every cache and the loop behind it."""

    run: RunOnPageLoop
    resolve_connection: Callable[[str | None, ConnectionOverride], LlmConnection]
    page_bearer: Callable[[LlmConnection], BearerSource]


@dataclass(frozen=True, slots=True)
class PageConnectionActions:
    """The page side of ``ConnectionActions``: the seams ride here, the loop and caches in hooks."""

    config: str | None
    connection: LlmConnection
    bearer: BearerSource
    list_seam: ListModels | None  # the connection_list_models AppTest seam
    transport: httpx.BaseTransport | None  # the connection_transport AppTest seam
    hooks: PageConnectionHooks

    def resolve(self, override: ConnectionOverride) -> LlmConnection:
        return self.hooks.resolve_connection(self.config, override)

    def list_models(self, candidate: LlmConnection) -> ModelListing:
        bearer = self.hooks.page_bearer(candidate)
        try:
            return self.hooks.run(
                cached_model_listing(candidate, bearer, list_models=self.list_seam)
            )
        except BEARER_ERRORS as exc:  # E1 / E4 / E5: shown in the caption, never raised
            return ModelListing((), redacted_failure_caption(exc, bearer), 0.0)

    def refresh_models(self, candidate: LlmConnection) -> ModelListing:
        clear_model_listing_cache(candidate)
        return self.list_models(candidate)

    def test(self, candidate: LlmConnection) -> str:
        bearer = self.hooks.page_bearer(candidate)
        return self.hooks.run(run_connection_test(candidate, bearer, transport=self.transport))

    def renew(self) -> str | None:
        """None once a new token is cached (the auth row shows its time); else the caption."""
        before = self.bearer.describe().renewed_at
        try:
            self.bearer.renew(self.bearer.peek() or None, reason="manual")
        except BEARER_ERRORS as exc:  # the Protocol's failure family, not one member of it
            return f"renew failed: {redact_bearer(str(exc), self.bearer)}"
        if self.bearer.describe().renewed_at == before:  # H3: answered from the bearer's cache
            return NOTHING_RENEWED
        clear_model_listing_cache(self.connection)
        return None


def dialog_actions(
    config: str | None,
    connection: LlmConnection,
    bearer: BearerSource,
    hooks: PageConnectionHooks,
) -> ConnectionActions:
    """The callbacks the dialog needs, bound to this page's connection and bearer."""
    return PageConnectionActions(
        config,
        connection,
        bearer,
        st.session_state.get("connection_list_models"),
        st.session_state.get("connection_transport"),
        hooks,
    )


__all__ = ("PageConnectionActions", "PageConnectionHooks", "dialog_actions")
