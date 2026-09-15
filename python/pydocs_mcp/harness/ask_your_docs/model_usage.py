"""What each model message cost — the binding's usage record, persisted beside the trace.

The raw server recorder records what the SERVER saw. It never sees the
conversation, so it cannot record what the MODEL spent: the prompt and
completion tokens, the reasoning tokens a thinking model billed, the cached
prefix the endpoint reused, or the price the endpoint quoted. Those live on the
model messages, and the binding is the one place holding both the finished
message list and the trace the run just wrote — the same reason
``model_turns.py`` does its join here.

So this module folds the messages into one accounting record per model message
and lands it in a sidecar (``model_usage.json``) beside the trace, exactly like
the turn sidecar. The eval suite reads it back into its own per-message usage
accounting, where the existing dedupe-by-message-id rule applies unchanged.

**Absent is not zero.** The sidecar is written even when no message reported
usage, so a reader can tell "this endpoint quoted nothing" (present, empty)
apart from "this run predates the sidecar" (absent) — the same
undefined-versus-zero distinction the report's ``n/a`` cells make.

**Subsets, not addends.** ``reasoning_tokens`` is the thinking slice OF
``output_tokens`` and the cache counts are slices OF ``input_tokens`` (the
OpenAI-format ``completion_tokens_details`` / ``prompt_tokens_details``
convention that langchain's ``usage_metadata`` mirrors). They are recorded for
diagnosis; adding them to their parent would bill the same token twice.

Duck-typed on the message's ``type`` tag (``"ai"``), like ``model_turns`` and
``activity_events``: this module imports no langchain, so the fold costs nothing
on a core install.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The sidecar the binding writes beside the raw server capture. The FORMAT is
# the contract across the packaging boundary (the ADR 0009 placement rule the
# blob store, the events file and the turn sidecar already follow) — the eval
# reader mirrors this name rather than importing it.
MODEL_USAGE_FILENAME = "model_usage.json"
MODEL_USAGE_SCHEMA_VERSION = 1

# langchain's tag for a model message. Duck-typed rather than imported so this
# module stays free of the optional agent runtime.
_AI_MESSAGE_TYPE = "ai"

# ``usage_metadata`` sub-mappings: the thinking slice of the completion, and the
# cache slices of the prompt (langchain's normalized spelling of the
# OpenAI-format ``*_tokens_details`` blocks).
_OUTPUT_DETAILS_KEY = "output_token_details"
_INPUT_DETAILS_KEY = "input_token_details"
_REASONING_DETAIL = "reasoning"
_CACHE_READ_DETAIL = "cache_read"
_CACHE_CREATION_DETAIL = "cache_creation"

# Where an endpoint that quotes a price puts it. OpenRouter returns
# ``usage.cost`` only when the request asks for usage accounting, and this
# harness's chat wire sends no such request body today — so on that endpoint the
# quote is genuinely absent rather than misplaced, and these paths are what makes
# the column fill in by itself the day the wire starts asking. The OpenAI client
# keeps unknown usage fields, so the quote would reach ``response_metadata``
# under whichever of these the client version chose. An endpoint that quotes no
# price matches none of them and the record reads ``None`` — never ``0.0``,
# which would claim a measured free run.
_COST_PATHS: tuple[tuple[str, ...], ...] = (("cost",), ("token_usage", "cost"), ("usage", "cost"))


@dataclass(frozen=True, slots=True)
class MessageUsage:
    """One model message's token spend, as the endpoint reported it.

    ``turn`` is 1-based over model messages, the same count the turn sidecar
    and ``Trajectory.turns`` use. ``message_id`` is the endpoint's own id when
    it sent one, so a retry that re-appends the same message cannot be counted
    twice downstream. ``reasoning_tokens`` and ``reported_cost_usd`` are
    ``None`` when the endpoint reported no such figure.
    """

    turn: int
    message_id: str | None
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    reported_cost_usd: float | None

    def to_dict(self) -> dict[str, Any]:
        """The sidecar's record shape — the cross-package contract."""
        return {
            "turn": self.turn,
            "message_id": self.message_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "reported_cost_usd": self.reported_cost_usd,
        }


def message_usages(messages: Iterable[Any]) -> tuple[MessageUsage, ...]:
    """Every model message that reported usage, in message order, turn-stamped.

    A model message whose endpoint reported no ``usage_metadata`` yields NO
    record — recording zeros for it would state a measurement the endpoint
    never made — but it still advances the turn count, so the turns here line
    up with the turn sidecar's.

    Example:
        >>> class _Msg:
        ...     type = "ai"
        ...     usage_metadata = {"input_tokens": 9, "output_tokens": 3}
        >>> message_usages([_Msg()])[0].input_tokens
        9
    """
    usages: list[MessageUsage] = []
    turn = 0
    for message in messages:
        if getattr(message, "type", "") != _AI_MESSAGE_TYPE:
            continue
        turn += 1
        usage = _usage_of(message, turn)
        if usage is not None:
            usages.append(usage)
    return tuple(usages)


def _usage_of(message: Any, turn: int) -> MessageUsage | None:
    """This message's accounting record, or ``None`` when it reported no usage."""
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        return None
    output_details = _details(usage, _OUTPUT_DETAILS_KEY)
    input_details = _details(usage, _INPUT_DETAILS_KEY)
    return MessageUsage(
        turn=turn,
        message_id=_message_id(message),
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        reasoning_tokens=_optional_count(output_details.get(_REASONING_DETAIL)),
        cache_read_input_tokens=_count(input_details.get(_CACHE_READ_DETAIL)),
        cache_creation_input_tokens=_count(input_details.get(_CACHE_CREATION_DETAIL)),
        reported_cost_usd=reported_cost_usd(getattr(message, "response_metadata", None)),
    )


def _details(usage: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """One ``*_token_details`` sub-mapping, or an empty one when absent."""
    details = usage.get(key)
    return details if isinstance(details, Mapping) else {}


def _message_id(message: Any) -> str | None:
    """The endpoint's message id, or ``None`` when the message carries none."""
    identifier = getattr(message, "id", None)
    return str(identifier) if identifier else None


def _count(raw: Any) -> int:
    """A token count, defaulting an absent or non-integer field to ``0``."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0


def _optional_count(raw: Any) -> int | None:
    """A token count the endpoint may not report at all (``None``, not ``0``)."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def reported_cost_usd(metadata: Any) -> float | None:
    """The price the endpoint quoted for one message, or ``None`` when it quoted none.

    Example:
        >>> reported_cost_usd({"token_usage": {"cost": 0.25}})
        0.25
        >>> reported_cost_usd({"token_usage": {"total_tokens": 10}}) is None
        True
    """
    if not isinstance(metadata, Mapping):
        return None
    for path in _COST_PATHS:
        quoted = _follow(metadata, path)
        if isinstance(quoted, (int, float)) and not isinstance(quoted, bool):
            return float(quoted)
    return None


def _follow(metadata: Mapping[str, Any], path: Sequence[str]) -> Any:
    """Walk ``path`` through nested mappings; ``None`` the moment one is missing."""
    current: Any = metadata
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def write_model_usage(trace_dir: Path, usages: Sequence[MessageUsage]) -> Path:
    """Persist the per-message usage beside the trace; return the file written.

    Canonical JSON, written even for an empty sequence so the file's presence
    means "usage was folded for this run" rather than "this run spent nothing".
    """
    payload = {
        "schema_version": MODEL_USAGE_SCHEMA_VERSION,
        "messages": [usage.to_dict() for usage in usages],
    }
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / MODEL_USAGE_FILENAME
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


__all__ = (
    "MODEL_USAGE_FILENAME",
    "MODEL_USAGE_SCHEMA_VERSION",
    "MessageUsage",
    "message_usages",
    "reported_cost_usd",
    "write_model_usage",
)
