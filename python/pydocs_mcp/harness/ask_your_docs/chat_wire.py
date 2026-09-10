"""What the chat model is actually sent: ``ChatParamsConfig`` + ``ControlSupport`` → the wire.

Model-params v2 §3.1 (the mapping), §5 rule 2 (D7: a saved value for a hidden
control is not sent, and nothing but the Test line and one JSON log line says
so) and §6 rule 3 (eval reads the same function over the static tables). The
ONE resolver: the dialog's Test line, the page and eval all call
:func:`resolve_wire`, so they cannot disagree about what is sent.

Phase 1 sends first-class ``ChatOpenAI`` fields only, identical on every
profile; ``extra_body`` stays ``None`` until the D4 routes land (its own PR).

Example:
    >>> from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
    >>> wire, not_sent = resolve_wire(ChatParamsConfig(thinking="off"), ControlSupport())
    >>> wire.first_class, not_sent
    ((('reasoning_effort', 'none'),), ())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport, support_for
from pydocs_mcp.harness.ask_your_docs.provider_profiles import (
    DisplayProfile,
    ProviderProfile,
    wire_profile,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import (
    ChatParamsConfig,
    ThinkingLevel,
)

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")

# The control order IS the config's field order — one source for the log, the summary and the loop.
_PARAM_NAMES: tuple[str, ...] = tuple(ChatParamsConfig.model_fields)
# YAML key → ChatOpenAI field. ``max_tokens`` keeps its name: LangChain itself sends it as
# max_completion_tokens (``_WIRE_FIELD``), and D6 measured OpenRouter honouring that field.
_CHAT_MODEL_FIELD = {"thinking": "reasoning_effort"}
_WIRE_FIELD = {"max_tokens": "max_completion_tokens"}
_OFF_EFFORT = "none"  # Thinking Off on every profile in phase 1 (§3.2)

STARVATION_MESSAGE = (
    "The reply ran out of tokens while thinking. Raise Max output tokens or turn Thinking down."
)


@dataclass(frozen=True, slots=True)
class WireParams:
    """The resolved request settings; hashable, so it can key the agent cache (§5 rule 8)."""

    first_class: tuple[tuple[str, Any], ...] = ()  # sorted (ChatOpenAI field, value) pairs
    extra_body: None = None  # phase 2 (D4) fills this; always None in phase 1
    thinking_off: bool = False  # the panel's "Reasoning: off (your setting)" (§7)

    def chat_model_kwargs(self) -> dict[str, Any]:
        """The ``ChatOpenAI`` constructor kwargs — empty for :data:`NO_WIRE_PARAMS` (AC-19)."""
        return dict(self.first_class)

    @property
    def sent_params(self) -> tuple[str, ...]:
        """The YAML names that are sent, in control order (names only — never values)."""
        fields = {field for field, _ in self.first_class}
        return tuple(name for name in _PARAM_NAMES if _chat_model_field(name) in fields)

    def request_fields(self) -> dict[str, Any]:
        """The sent fields under every name a 400 may use for them — the rejection parser's input."""
        fields = dict(self.first_class)
        return {**fields, **{_WIRE_FIELD[n]: v for n, v in fields.items() if n in _WIRE_FIELD}}

    def wire_fields(self) -> dict[str, Any]:
        """The sent fields under the request body's own names (``max_completion_tokens``)."""
        return {_WIRE_FIELD.get(field, field): value for field, value in self.first_class}


NO_WIRE_PARAMS = WireParams()  # frozen: one shared "send nothing beyond the model"


def _chat_model_field(name: str) -> str:
    return _CHAT_MODEL_FIELD.get(name, name)


def wire_field_name(name: str) -> str:
    """A control's name on the wire, e.g. ``wire_field_name("thinking") == "reasoning_effort"``."""
    return _WIRE_FIELD.get(name, _chat_model_field(name))


def resolve_wire(
    params: ChatParamsConfig, support: ControlSupport
) -> tuple[WireParams, tuple[str, ...]]:
    """``(wire, not_sent)``: every configured value a shown control can carry, and the rest's names.

    Temperature / Top p follow the Thinking that is actually SENT, so a hidden Thinking
    value never unlocks sampling it would have forbidden.
    """
    thinking = params.thinking if params.thinking in support.thinking_options else None
    sent: list[tuple[str, Any]] = []
    not_sent: list[str] = []
    for name in _PARAM_NAMES:
        value = getattr(params, name)
        if value is None:
            continue
        if _control_shown(name, support, thinking):
            sent.append((_chat_model_field(name), wire_value(value)))
        else:
            not_sent.append(name)
    if not sent:
        return NO_WIRE_PARAMS, tuple(not_sent)
    return WireParams(tuple(sorted(sent)), None, thinking is ThinkingLevel.OFF), tuple(not_sent)


def _control_shown(name: str, support: ControlSupport, thinking: ThinkingLevel | None) -> bool:
    shown = {
        "thinking": thinking is not None,
        "temperature": support.temperature_shown(thinking),
        "max_tokens": support.show_max_tokens,
        "top_p": support.top_p_shown(thinking),
        "seed": support.show_seed,
    }
    return shown[name]


def wire_value(value: Any) -> Any:
    """A param's value as sent: a Thinking level becomes its effort, e.g. Off → ``"none"``."""
    if isinstance(value, ThinkingLevel):  # On is stored as medium (D8), so no label reaches here
        return _OFF_EFFORT if value is ThinkingLevel.OFF else value.value
    return value


def static_support(wire: ProviderProfile, model: str | None) -> ControlSupport:
    """The network-free support: the wire profile + the frozen tables (eval, and the default)."""
    return support_for(DisplayProfile(wire), model or "")


def unhonoured_by_tables(
    params: ChatParamsConfig, wire: ProviderProfile, model: str | None
) -> tuple[str, ...]:
    """The configured params the static tables would hide — eval raises on these (§6 rule 3)."""
    return resolve_wire(params, static_support(wire, model))[1]


def connection_wire(connection: LlmConnection, support: ControlSupport | None = None) -> WireParams:
    """The connection's wire; ``support`` None = the static tables. Logs what was dropped (D7)."""
    if support is None:
        profile = wire_profile(connection.provider, connection.base_url)
        support = static_support(profile, connection.model)
    wire, not_sent = resolve_wire(connection.params, support)
    log_chat_params_effective(wire, not_sent)
    return wire


def log_chat_params_effective(wire: WireParams, not_sent: tuple[str, ...]) -> None:
    """One JSON line, names only; silent when nothing was configured (no-params logs stay as today)."""
    if not wire.first_class and not not_sent:
        return
    fields = {"sent": list(wire.sent_params), "not_sent": list(not_sent)}
    log.info(json.dumps({"event": "chat_params_effective", **fields}))


def wire_summary(wire: WireParams) -> str:
    """The Test line's tail: ``sent reasoning_effort=low, temperature=0.2, …`` in control order."""
    values = dict(wire.first_class)
    parts = [
        f"{wire_field_name(name)}={values[_chat_model_field(name)]}" for name in wire.sent_params
    ]
    return f"sent {', '.join(parts)}" if parts else "sent nothing beyond the model"


def reply_starved(reply: Any, wire: WireParams) -> bool:
    """§5 rule 6: ``finish_reason == "length"`` with empty content while Thinking is not Off."""
    metadata = getattr(reply, "response_metadata", None) or {}
    empty = not str(getattr(reply, "content", "")).strip()
    return empty and metadata.get("finish_reason") == "length" and not wire.thinking_off


__all__ = (
    "NO_WIRE_PARAMS",
    "STARVATION_MESSAGE",
    "WireParams",
    "connection_wire",
    "log_chat_params_effective",
    "reply_starved",
    "resolve_wire",
    "static_support",
    "unhonoured_by_tables",
    "wire_field_name",
    "wire_summary",
    "wire_value",
)
