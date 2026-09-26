"""What a trajectory spent: tokens in and out, and the two dollar figures.

The ask-your-docs binding folds each model message's usage into a
``model_usage.json`` sidecar beside the trace, for the same reason it folds the
model turns there: the server never sees the conversation, so what the MODEL
spent exists nowhere in the raw capture. This module is the reader of that
sidecar, and it deliberately reads it into :class:`LoopEvent` values rather than
into a private total — so the suite's ONE token accounting
(``metrics.deduped_token_totals``, dedupe-by-``message_id``, ADR 0010) applies
to the ask path unchanged, instead of a second summation drifting beside it.

Two dollar figures, never conflated:

- **estimated** — the run's own price flags applied to the measured tokens. A
  deployment-supplied price times a measured count; zero when no price was
  given, which is the honest reading of "priced at nothing per token".
- **reported** — what the ENDPOINT quoted. ``None`` whenever it quoted nothing,
  which is the ordinary case: the ask harness talks OpenAI-format to endpoints
  that mostly return no price at all, and an OpenRouter endpoint returns
  ``usage.cost`` only when the request asks for usage accounting.

Reasoning and cache counts are SLICES of their parents (reasoning of the
completion, cache reads of the prompt), so the estimate prices ``input_tokens``
and ``output_tokens`` only — reasoning is already billed inside the completion
count at the output rate, and adding it again would double-charge.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.metrics import (
    REASONING_KEY,
    USAGE_KEYS,
    TokenTotals,
    deduped_token_totals,
)
from pydocs_eval.trajectory.schema import LoopEvent, TrajectoryError

# Mirrors ``pydocs_mcp.harness.ask_your_docs.model_usage.MODEL_USAGE_FILENAME``.
# Mirrored and not imported: the eval package keeps a zero-``pydocs_mcp`` floor
# and the sidecar's FORMAT is the contract (the ADR 0009 placement rule the blob
# store and the turn sidecar already follow).
ASK_MODEL_USAGE_FILENAME = "model_usage.json"
_MESSAGES_KEY = "messages"
# The record field issue #371 adds (``MessageUsage.finish_reason``); absent from a
# sidecar written before it, which is why every reader of it defaults.
_FINISH_REASON_KEY = "finish_reason"

# Per-token prices are quoted per million tokens, everywhere the suite states
# one — the plan's flags, the report's columns and this estimator.
_TOKENS_PER_PRICED_UNIT = 1_000_000

# The sidecar record fields that become a ``LoopEvent.usage`` mapping — taken
# from the summing keys themselves rather than respelled here, because a name
# that drifted apart from them would make this reader contribute a silent zero
# instead of failing. A record spelling a field differently contributes nothing.
_USAGE_FIELDS = (*USAGE_KEYS, REASONING_KEY)

# ``LoopEvent.kind`` for a model message. NOT ``"result"``: that kind is the
# loop client's run-total envelope, which ``deduped_token_totals`` excludes by
# design, so an ask message filed under it would vanish from the computed sum.
_ASSISTANT_KIND = "assistant"


class MalformedUsageSidecarError(TrajectoryError, ValueError):
    """A usage sidecar that exists but cannot be read as version 1.

    Raised rather than degraded to zero: a sidecar the writer produced and the
    reader cannot parse is a capture defect, and a zero total would read like a
    measured free run.
    """


@dataclass(frozen=True, slots=True)
class TokenAccount:
    """One trajectory's measured spend: its token totals and both cost figures.

    ``reported_usd`` is ``None`` when the endpoint quoted no price for any
    message of the trajectory; ``tokens.reasoning_tokens`` is ``None`` when no
    message reported a reasoning count.
    """

    tokens: TokenTotals
    estimated_usd: float
    reported_usd: float | None

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion. Reasoning and cache counts are slices of those."""
        return self.tokens.input_tokens + self.tokens.output_tokens

    @property
    def cached_tokens(self) -> int:
        """Cache reads plus cache writes — the prompt slice the endpoint cached."""
        return self.tokens.cache_read_input_tokens + self.tokens.cache_creation_input_tokens


def priced_usd(
    *, input_tokens: int, output_tokens: int, usd_per_1m_input: float, usd_per_1m_output: float
) -> float:
    """Dollars for a token count at per-million rates; ``0.0`` with no price given.

    Example:
        >>> priced_usd(input_tokens=1_000_000, output_tokens=0,
        ...     usd_per_1m_input=2.0, usd_per_1m_output=8.0)
        2.0
    """
    priced = input_tokens * usd_per_1m_input + output_tokens * usd_per_1m_output
    return priced / _TOKENS_PER_PRICED_UNIT


def read_ask_usage_events(trace_dir: Path) -> tuple[LoopEvent, ...] | None:
    """One ask trajectory's per-message usage as loop events, or ``None``.

    ``None`` means the trajectory recorded NO sidecar — it predates the fold or
    ran under a harness that does not write one — which is undefined spend, not
    zero spend. An endpoint that simply quoted nothing writes an EMPTY sidecar
    and yields ``()``, whose totals are a measured zero.

    Raises:
        MalformedUsageSidecarError: a sidecar that exists but is unreadable.
    """
    path = trace_dir / ASK_MODEL_USAGE_FILENAME
    if not path.is_file():
        return None
    return tuple(_loop_event(record, path) for record in _records(path))


def _records(path: Path) -> list[Mapping[str, Any]]:
    """The sidecar's message list, or a typed error naming what writes it."""
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise MalformedUsageSidecarError(
            f"unreadable {ASK_MODEL_USAGE_FILENAME} at {path} ({exc}); the "
            "ask-your-docs binding writes it beside the trace"
        ) from exc
    raw = payload.get(_MESSAGES_KEY) if isinstance(payload, Mapping) else None
    if not isinstance(raw, list):
        raise MalformedUsageSidecarError(
            f"{path} has no {_MESSAGES_KEY!r} list; expected "
            '{"schema_version": 1, "messages": [{"turn": 1, "input_tokens": 0, ...}]}'
        )
    return [record for record in raw if isinstance(record, Mapping)]


def _loop_event(record: Mapping[str, Any], path: Path) -> LoopEvent:
    """One sidecar record as the loop event the token accounting already sums."""
    turn = record.get("turn")
    if not isinstance(turn, int) or isinstance(turn, bool):
        raise MalformedUsageSidecarError(
            f"{path}: usage record {record!r} has turn {turn!r}, expected an int"
        )
    message_id = record.get("message_id")
    return LoopEvent(
        # The sidecar is keyed by turn, and one model message is one turn, so
        # the turn IS this record's identity within the trajectory.
        event_id=f"usage-{turn}",
        trajectory_id=path.parent.name,
        kind=_ASSISTANT_KIND,
        turn=turn,
        message_id=str(message_id) if isinstance(message_id, str) else None,
        usage={field: record[field] for field in _USAGE_FIELDS if field in record},
    )


def _sum_quotes(quotes: Iterable[Any]) -> float | None:
    """Sum the numeric quotes; ``None`` when not one message carried a price."""
    total: float | None = None
    for quote in quotes:
        if isinstance(quote, (int, float)) and not isinstance(quote, bool):
            total = (total or 0.0) + float(quote)
    return total


def account_for_trace(
    trace_dir: Path, *, usd_per_1m_input: float, usd_per_1m_output: float
) -> TokenAccount | None:
    """One ask trajectory's token and cost account, or ``None`` when unrecorded.

    ``None`` propagates the undefined-not-zero rule up to the report, where it
    prints as ``n/a`` and drops out of the means instead of pulling them down.

    The tokens and the endpoint's quote come off the SAME records, so the file
    is parsed once here and both are read from that one pass.
    """
    path = trace_dir / ASK_MODEL_USAGE_FILENAME
    if not path.is_file():
        return None
    records = _records(path)
    return account_for_events(
        tuple(_loop_event(record, path) for record in records),
        reported=_sum_quotes(record.get("reported_cost_usd") for record in records),
        usd_per_1m_input=usd_per_1m_input,
        usd_per_1m_output=usd_per_1m_output,
    )


def last_finish_reason(trace_dir: Path) -> str:
    """How the trajectory's last METERED reply finished; ``""`` when none recorded it.

    The product records a reply's ``finish_reason`` in the usage sidecar from
    issue #371 on; a sidecar written before that, a trajectory with no sidecar, and
    one whose endpoint metered no reply all answer ``""`` — "not recorded",
    which the outcome taxonomy reads as "not starved", never as a guess.

    Raises:
        MalformedUsageSidecarError: a sidecar that exists but is unreadable.
    """
    path = trace_dir / ASK_MODEL_USAGE_FILENAME
    if not path.is_file():
        return ""
    records = _records(path)
    reason = records[-1].get(_FINISH_REASON_KEY) if records else None
    return reason if isinstance(reason, str) else ""


def account_for_events(
    events: Sequence[LoopEvent],
    *,
    reported: float | None,
    usd_per_1m_input: float,
    usd_per_1m_output: float,
) -> TokenAccount:
    """Total ``events`` through the suite's ONE dedupe, and price the result.

    Deduping here is what makes a retried model message cost once: the endpoint
    re-sends the same ``message_id``, and the shared accounting counts each id
    exactly once (ADR 0010).
    """
    tokens = deduped_token_totals(events)
    return TokenAccount(
        tokens=tokens,
        estimated_usd=priced_usd(
            input_tokens=tokens.input_tokens,
            output_tokens=tokens.output_tokens,
            usd_per_1m_input=usd_per_1m_input,
            usd_per_1m_output=usd_per_1m_output,
        ),
        reported_usd=reported,
    )


__all__ = (
    "ASK_MODEL_USAGE_FILENAME",
    "MalformedUsageSidecarError",
    "TokenAccount",
    "account_for_events",
    "account_for_trace",
    "last_finish_reason",
    "priced_usd",
    "read_ask_usage_events",
)
