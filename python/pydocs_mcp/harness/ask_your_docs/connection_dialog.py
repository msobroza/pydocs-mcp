"""The sidebar's connection status line and the Connection dialog (design §4.9, R7).

Streamlit-only rendering: the page injects every action (``ConnectionActions``),
so this module never touches the event loop, the bearer registry or the caches.
Imported by the page path alone — the launcher's lazy-import contract (AC-24)
keeps ``streamlit`` out of ``cli.py``.
"""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerStatus, display_host
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride, LlmConnection
from pydocs_mcp.harness.ask_your_docs.model_listing import ModelListing
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

# Widget keys — AppTest addresses widgets by key.
KEY_OPEN = "connection_open"
KEY_BASE_URL = "connection_dialog_base_url"
KEY_RENEW = "connection_renew"
KEY_MODEL = "connection_dialog_model"
KEY_MODEL_TEXT = "connection_dialog_model_text"
KEY_REFRESH = "connection_refresh_models"
KEY_TEST = "connection_test"
KEY_APPLY = "connection_apply"
# Session-state keys — never persisted; a browser reload starts from precedence again.
STATE_DIALOG_OPEN = "connection_dialog_open"
STATE_OVERRIDE = "connection_override"
STATE_TEST_RESULT = "connection_test_result"  # the dialog's one outcome caption: Test, Renew

ORIGIN_NOTE = "⚠ endpoint differs from ask_your_docs.llm.base_url"
CLEARTEXT_NOTE = "⚠ http"
NOT_CHOSEN = "model: not chosen"
# A status-line label, not a credential (S105 keys on the name).
TOKEN_UNAVAILABLE = "token unavailable ⚠"  # noqa: S105
NOTHING_RENEWED = (
    "nothing renewed: the last renewal is still within the bearer's rate limit; "
    "try again in a few seconds"
)


class ConnectionActions(Protocol):
    """What the dialog can do — injected by the page (the event loop stays in app.py)."""

    def resolve(self, override: ConnectionOverride) -> LlmConnection: ...  # the dialog's candidate
    def list_models(self, candidate: LlmConnection) -> ModelListing: ...
    def refresh_models(self, candidate: LlmConnection) -> ModelListing: ...  # evict, then list
    def test(self, candidate: LlmConnection) -> str: ...  # the caption text (design E11)
    def renew(self) -> str | None: ...  # None once renewed (the row shows it), else the caption


def auth_cell(
    connection: LlmConnection, status: BearerStatus, *, bearer_error: str | None = None
) -> str:
    """The status line's auth cell (design §4.9): mode, last four inline, renewal time, the notes."""
    if bearer_error is not None:
        cell = TOKEN_UNAVAILABLE
    elif connection.auth_mode is AuthMode.TOKEN_SERVICE:
        cell = f"token …{status.last_four} {_renewed(status)}"
    elif connection.auth_mode is AuthMode.ENV_KEY:
        cell = _env_key_cell(connection, status)
    else:
        cell = "no auth"
    if connection.cleartext_bearer:  # H2
        cell += f" {CLEARTEXT_NOTE}"
    if connection.origin_changed:  # H1 — always last: a cell that carries both ENDS with it
        cell += f" {ORIGIN_NOTE}"
    return cell


def _renewed(status: BearerStatus) -> str:
    return status.renewed_at.strftime("%H:%M") if status.renewed_at else "pending"


def _env_key_cell(connection: LlmConnection, status: BearerStatus) -> str:
    if not connection.block_present and not status.last_four:
        return "no auth"  # the lenient no-block form with the variable unset
    state = "set" if status.last_four else "missing"
    return f"${connection.api_key_env} {state}"


def vision_cell(capabilities: ModelCapabilities | None) -> str:
    """Today's badge text with its source; ``?`` while no verdict exists (bearer unavailable)."""
    if capabilities is None:
        return "vision: ?"
    return f"vision: {'yes' if capabilities.multimodal else 'no'} ({capabilities.source})"


def render_connection_status_line(
    connection: LlmConnection,
    status: BearerStatus,
    capabilities: ModelCapabilities | None,
    *,
    bearer_error: str | None = None,
) -> None:
    """One ``st.caption``: host · model · auth · vision (the E1 text rides ``help=``)."""
    cells = [
        display_host(connection.base_url),
        connection.model or NOT_CHOSEN,
        auth_cell(connection, status, bearer_error=bearer_error),
        vision_cell(capabilities),
    ]
    st.caption(" · ".join(cells), help=bearer_error)


@st.dialog("Connection", width="small")
def open_connection_dialog(
    connection: LlmConnection,
    status: BearerStatus,
    actions: ConnectionActions,
    *,
    vision_text: str,
    bearer_error: str | None = None,
) -> None:
    """The dialog body (design §4.9): Base URL, auth row (+ Renew), Model, status, Test, Apply."""
    base_url = _render_base_url_field(connection)
    _render_auth_row(connection, status, actions, bearer_error=bearer_error)
    candidate = _candidate_connection(actions, base_url)
    if candidate is None:
        return  # the caption named the offending URL; nothing to list, test or apply
    listing = _dialog_listing(actions, candidate, bearer_error)
    model = _render_model_picker(candidate, listing, actions)
    st.caption(f"{_listing_caption(listing)} · {vision_text}")
    _render_outcome_row(actions, base_url, model)
    _render_apply_button(base_url, model)


def _render_base_url_field(connection: LlmConnection) -> str:
    return st.text_input(
        "Base URL",
        value=connection.base_url or "",
        key=KEY_BASE_URL,
        help="session only; the bearer is sent to whatever endpoint you enter",
    )


def _dialog_listing(
    actions: ConnectionActions, candidate: LlmConnection, bearer_error: str | None
) -> ModelListing:
    """The ids to offer. A bearer that already failed at render short-circuits the call: it would
    re-pay the bearer's retry envelope on every dialog rerun to bring the same text back."""
    if bearer_error:
        return ModelListing((), bearer_error, 0.0)  # E1 / E4 / E5, captioned like a listing failure
    return actions.list_models(candidate)


def _candidate_connection(actions: ConnectionActions, base_url: str) -> LlmConnection | None:
    """The connection the dialog's Base URL resolves to — or None after captioning a URL that
    ``urlsplit`` rejects (``http://[bad``), so typed input can never crash the page."""
    try:
        urlsplit(base_url)  # explicit guard: not a side effect of the resolver's own logging
        return actions.resolve(ConnectionOverride(base_url=base_url or None))
    except ValueError as exc:
        st.caption(f"invalid base URL {base_url!r}: {exc}")
        return None


def _render_auth_row(
    connection: LlmConnection,
    status: BearerStatus,
    actions: ConnectionActions,
    *,
    bearer_error: str | None,
) -> None:
    left, right = st.columns([4, 1])
    left.caption(_auth_row_text(connection, status, bearer_error=bearer_error))
    # Renew exists only for a configured token service (D7/R7): an environment
    # key is re-read on every request already.
    if connection.auth_mode is AuthMode.TOKEN_SERVICE and right.button("Renew", key=KEY_RENEW):
        _renew(actions)


def _renew(actions: ConnectionActions) -> None:
    """Renew, then re-render: the row's time moves on success; otherwise the caption says why."""
    caption = actions.renew()
    if caption is None:
        st.session_state.pop(STATE_TEST_RESULT, None)  # an older outcome is stale now
    else:
        st.session_state[STATE_TEST_RESULT] = caption
    st.rerun()


def _auth_row_text(
    connection: LlmConnection, status: BearerStatus, *, bearer_error: str | None
) -> str:
    if connection.auth_mode is AuthMode.TOKEN_SERVICE and bearer_error is None:
        return f"token …{status.last_four} · renewed {_renewed(status)}"  # D4: both inline
    return auth_cell(connection, status, bearer_error=bearer_error)


def _render_model_picker(
    candidate: LlmConnection, listing: ModelListing, actions: ConnectionActions
) -> str:
    left, right = st.columns([5, 1])
    ids = list(listing.model_ids)
    if ids:
        index = ids.index(candidate.model) if candidate.model in ids else 0
        chosen = left.selectbox("Model", options=ids, index=index, key=KEY_MODEL)
    else:  # E6: the text-field fallback — a failed listing, or one that offered no id
        chosen = left.text_input("Model", value=candidate.model or "", key=KEY_MODEL_TEXT)
    if right.button("↻", key=KEY_REFRESH, help="refresh the model list"):
        actions.refresh_models(candidate)
        st.rerun()
    return chosen or ""


def _listing_caption(listing: ModelListing) -> str:
    if listing.error is not None:
        return f"listing failed: {listing.error}"
    return f"{len(listing.model_ids)} models listed"


def _render_outcome_row(actions: ConnectionActions, base_url: str, model: str) -> None:
    """Test connection — one round-trip on the dialog's candidate (E11) — and, below it, the
    outcome caption Test and Renew share (kept in session state so AppTest can read it)."""
    if st.button("Test connection", key=KEY_TEST):
        chosen = actions.resolve(ConnectionOverride(base_url=base_url or None, model=model or None))
        result = actions.test(chosen)
        st.session_state[STATE_TEST_RESULT] = (
            f"{result} {ORIGIN_NOTE}" if chosen.origin_changed else result
        )
    if STATE_TEST_RESULT in st.session_state:
        st.caption(st.session_state[STATE_TEST_RESULT])


def _render_apply_button(base_url: str, model: str) -> None:
    """Apply writes the session override and closes the dialog; the page re-resolves on rerun."""
    if not st.button("Apply", key=KEY_APPLY):
        return
    st.session_state[STATE_OVERRIDE] = ConnectionOverride(
        base_url=base_url or None, model=model or None
    )
    st.session_state[STATE_DIALOG_OPEN] = False
    st.rerun()


__all__ = (
    "CLEARTEXT_NOTE",
    "KEY_APPLY",
    "KEY_BASE_URL",
    "KEY_MODEL",
    "KEY_MODEL_TEXT",
    "KEY_OPEN",
    "KEY_REFRESH",
    "KEY_RENEW",
    "KEY_TEST",
    "NOTHING_RENEWED",
    "NOT_CHOSEN",
    "ORIGIN_NOTE",
    "STATE_DIALOG_OPEN",
    "STATE_OVERRIDE",
    "STATE_TEST_RESULT",
    "TOKEN_UNAVAILABLE",
    "ConnectionActions",
    "auth_cell",
    "open_connection_dialog",
    "render_connection_status_line",
    "vision_cell",
)
