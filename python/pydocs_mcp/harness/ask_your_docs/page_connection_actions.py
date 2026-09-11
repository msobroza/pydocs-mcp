"""The page side of the Connection dialog's ``ConnectionActions`` (design §4.9).

Moved out of ``app`` to keep that module inside its line budget. ``app`` keeps the
event loop and the cached functions these callbacks run through and injects them as
:class:`PageConnectionHooks`, so this module owns no Streamlit cache of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BEARER_ERRORS,
    BearerSource,
    redact_bearer,
    redacted_failure_caption,
)
from pydocs_mcp.harness.ask_your_docs.chat_wire import connection_wire
from pydocs_mcp.harness.ask_your_docs.connection_dialog import NOTHING_RENEWED, ConnectionActions
from pydocs_mcp.harness.ask_your_docs.family_presets import preset_for
from pydocs_mcp.harness.ask_your_docs.litellm_probe import GroupInfoSeam, litellm_group_row
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
from pydocs_mcp.harness.ask_your_docs.param_feedback import (
    EndpointFacts,
    learned_rejections,
    remember_endpoint_facts,
    restore_hidden_settings,
    session_support,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import display_profile, wire_profile
from pydocs_mcp.harness.ask_your_docs.settings_view import SettingsView, settings_placeholders

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
    group_info_seam: GroupInfoSeam | None = None  # the connection_group_info AppTest seam

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
        """One round-trip carrying exactly the wire Apply would send (model-params v2 §5 rule 4)."""
        bearer = self.hooks.page_bearer(candidate)
        wire = connection_wire(candidate, session_support(candidate))
        return self.hooks.run(
            run_connection_test(candidate, bearer, transport=self.transport, wire=wire)
        )

    def support_for(self, candidate: LlmConnection, listing: ModelListing) -> SettingsView:
        """What the chosen model honours; remembered so the page sends what the dialog shows."""
        entry = listing.entries_by_id.get(candidate.model or "")
        # A failed listing (or bearer) would fail the probe too: skip its retry envelope.
        row = None if listing.error is not None else self._litellm_row(candidate, entry)
        wire = wire_profile(candidate.provider, candidate.base_url)
        display = display_profile(wire, entry, row, declared=candidate.provider != "auto")
        remember_endpoint_facts(candidate, EndpointFacts(display, entry, row))
        support = session_support(candidate)
        hidden = len(learned_rejections(candidate))
        placeholders = settings_placeholders(entry, support)
        preset = preset_for(candidate.model or "", entry)
        return SettingsView(display.profile, support, placeholders, hidden, preset)

    def restore_hidden(self, candidate: LlmConnection) -> None:
        restore_hidden_settings(candidate)

    def _litellm_row(
        self, candidate: LlmConnection, entry: Mapping[str, Any] | None
    ) -> Mapping[str, Any] | None:
        """The LiteLLM probe (dialog only, v2 §2 step 5); a bearer failure is the listing's to show."""
        bearer = self.hooks.page_bearer(candidate)
        probe = litellm_group_row(
            candidate, bearer, entry, group_info=self.group_info_seam, transport=self.transport
        )
        try:
            return self.hooks.run(probe)
        except BEARER_ERRORS:
            return None

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
        st.session_state.get("connection_group_info"),
    )


__all__ = ("PageConnectionActions", "PageConnectionHooks", "dialog_actions")
