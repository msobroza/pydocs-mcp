"""What the chat page learns about the model settings during one session (model-params v2 §5).

Session state only, keyed by ``(base_url, model)``, never persisted:

- the endpoint facts the Connection dialog read (display profile, ``/models`` entry,
  LiteLLM row), so the page's agent is built with exactly the wire the dialog's Test line
  reported (rule 4) — before the dialog ever opens, the static tables decide;
- the controls a runtime 400 rejected (rule 5): hidden for the rest of the session,
  restorable from More, never retried automatically.

It also builds the two chat messages the page shows instead of any standing caption: the
rejection (mockup state G) and the starved reply (state H, rule 6).

Example:
    wire = page_wire(connection)
    caption = learn_param_rejection(exc, wire, connection) or redacted_failure_caption(exc, bearer)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.chat_wire import (
    STARVATION_MESSAGE,
    WireParams,
    log_chat_params_effective,
    reply_starved,
    resolve_wire,
    wire_field_name,
)
from pydocs_mcp.harness.ask_your_docs.control_support import (
    ControlSupport,
    rejected_control_from_error,
    support_for,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import DisplayProfile, wire_profile
from pydocs_mcp.harness.ask_your_docs.serve_session import leaf_exception
from pydocs_mcp.harness.ask_your_docs.settings_view import CONTROL_LABELS

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")  # app.py's logger

STATE_LEARNED_REJECTIONS = "connection_param_rejections"  # EndpointKey -> frozenset of controls
STATE_ENDPOINT_FACTS = "connection_endpoint_facts"  # EndpointKey -> EndpointFacts
# rejected_control_from_error reads a status-less error (LiteLLM in-process) as a 400 too.
_BAD_REQUEST = 400

EndpointKey = tuple[str | None, str | None]


@dataclass(frozen=True, slots=True)
class EndpointFacts:
    """What the dialog read about one (endpoint, model): the inputs of ``support_for``."""

    display: DisplayProfile
    entry: Mapping[str, Any] | None = None
    group_info: Mapping[str, Any] | None = None


def _endpoint_key(connection: LlmConnection) -> EndpointKey:
    return (connection.base_url, connection.model)


def _session_map(state_key: str) -> dict[EndpointKey, Any]:
    return st.session_state.setdefault(state_key, {})


def remember_endpoint_facts(connection: LlmConnection, facts: EndpointFacts) -> None:
    """The dialog's reading of this model, for the page's wire from now on."""
    _session_map(STATE_ENDPOINT_FACTS)[_endpoint_key(connection)] = facts


def learned_rejections(connection: LlmConnection) -> frozenset[str]:
    """The controls a 400 rejected for this model in this session."""
    return _session_map(STATE_LEARNED_REJECTIONS).get(_endpoint_key(connection), frozenset())


def restore_hidden_settings(connection: LlmConnection) -> None:
    """More's "Restore hidden settings (N)": this model's learned rejections are forgotten."""
    _session_map(STATE_LEARNED_REJECTIONS).pop(_endpoint_key(connection), None)


def session_support(connection: LlmConnection) -> ControlSupport:
    """The dialog's support for this model (static tables until it opened) + learned hiding."""
    facts = _session_map(STATE_ENDPOINT_FACTS).get(_endpoint_key(connection))
    if facts is None:
        facts = EndpointFacts(
            DisplayProfile(wire_profile(connection.provider, connection.base_url))
        )
    learned = learned_rejections(connection)
    return support_for(
        facts.display, connection.model or "", facts.entry, facts.group_info, learned
    )


def page_wire(connection: LlmConnection) -> WireParams:
    """What the page's agent is built with — the very wire the dialog's Test line reported."""
    return resolve_wire(connection.params, session_support(connection))[0]


def log_page_wire(connection: LlmConnection) -> None:
    """The D7 line (what is sent, what is not — names only), once per agent build."""
    log_chat_params_effective(*resolve_wire(connection.params, session_support(connection)))


def learn_param_rejection(
    exc: BaseException, wire: WireParams, connection: LlmConnection
) -> str | None:
    """A 400 naming a SENT field: hide that control for the session and return the chat message.

    None for any other failure (the page's redacted caption applies). Never retried (rule 5).
    """
    leaf = leaf_exception(exc)
    control = rejected_control_from_error(leaf, wire.request_fields())
    if control is None:
        return None
    rejections = _session_map(STATE_LEARNED_REJECTIONS)
    key = _endpoint_key(connection)
    rejections[key] = rejections.get(key, frozenset()) | {control}
    status = getattr(leaf, "status_code", _BAD_REQUEST)
    event = {"event": "chat_param_rejected", "param": wire_field_name(control), "status": status}
    log.warning(json.dumps(event))  # WARNING: `streamlit run` drops the page's INFO records
    return rejection_message(control, connection.model, status)


def rejection_message(control: str, model: str | None, status: int) -> str:
    """State G's one chat message, in the mockup's words."""
    return (
        f"The endpoint rejected {CONTROL_LABELS[control]} for {model} ({status}), "
        "so it's off for this session. Send your question again."
    )


@dataclass(slots=True)
class StarvationWatch:
    """``ask``'s ``on_final`` seam: keeps the turn's last message, then judges it (rule 6)."""

    wire: WireParams
    final: Any = None

    def observe(self, message: Any) -> None:
        self.final = message

    def answer_or_notice(self, answer: str) -> str:
        """The answer — or, for a reply that ran out of tokens while thinking, how to fix it."""
        if self.final is None or not reply_starved(self.final, self.wire):
            return answer
        log.warning(json.dumps({"event": "chat_reply_starved", "finish_reason": "length"}))
        return STARVATION_MESSAGE


__all__ = (
    "STATE_ENDPOINT_FACTS",
    "STATE_LEARNED_REJECTIONS",
    "EndpointFacts",
    "StarvationWatch",
    "learn_param_rejection",
    "learned_rejections",
    "log_page_wire",
    "page_wire",
    "rejection_message",
    "remember_endpoint_facts",
    "restore_hidden_settings",
    "session_support",
)
