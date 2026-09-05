"""Loop-side stream-json distiller (ADR 0010 loop-events + the usage trap).

Folds the headless-CLI ``--output-format stream-json`` stdout (one JSON object
per line) into an ordered tuple of ``DistilledLoopRecord``s that ``merge.py``
turns into canonical ``LoopEvent``s and joins to the server tool events.

Two load-bearing behaviors this module exists to get right:

1. **Usage on a dedicated per-message carrier record.** A real transcript emits
   one record per assistant *content block* (up to 5 per API message) and
   ``message.usage`` is byte-identical across every record of one ``message.id``.
   Summing usage per record over-counts input/cache tokens several-fold — the
   exact defect the Q&A-track ``_parse.py`` still carries. Here the message's
   usage rides a single dedicated assistant-kind carrier record (``message_id`` +
   ``usage``); every content-block record carries ``usage=None``. This does two
   jobs: a downstream sum dedupes for free, AND the usage survives ``merge.py``
   replacing MCP ``tool_use`` blocks with server ``ToolEvent``s (which carry no
   usage) — a message of only MCP tool uses would otherwise lose its usage
   entirely (ADR 0010).
2. **Tool-results sidecar awareness.** Oversized tool results spill to a
   ``<sessionId>/tool-results/<tool_use_id>.txt`` sidecar and the inline text is
   elided. When a ``sidecar_dir`` is supplied, a tool-result's text is read from
   the sidecar whenever that file exists — inline text is treated as possibly
   elided, never trusted blindly.

Pure and total: no subprocess, and malformed lines are skipped (a truncated run
must still distill partial records) — matching the existing ``_parse.py`` idiom.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The tool-name prefix the CLI stamps on MCP-provided tools (``mcp__srv__tool``).
# Bare tools (Read/Grep/Glob/Bash) carry no prefix. Single source of truth.
_MCP_TOOL_PREFIX = "mcp__"


@dataclass(frozen=True, slots=True)
class DistilledLoopRecord:
    """One ordered loop record before merge assigns it a trajectory_id/event_id.

    ``is_mcp`` distinguishes MCP tool uses (replaced by the server tool event at
    merge) from bare tool uses (emitted as loop events). ``usage`` rides only the
    per-message assistant carrier record; content-block records carry ``usage=None``."""

    kind: str
    turn: int
    message_id: str | None = None
    usage: dict[str, Any] | None = None
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    text: str | None = None
    is_error: bool | None = None
    is_mcp: bool = False


@dataclass(frozen=True, slots=True)
class StreamDistillation:
    """The distilled loop stream: ordered records + the final ``session_id``.

    ``session_id`` is the ``--session-id`` UUID echoed by the result envelope —
    the loop-side half of the trajectory correlation key (ADR 0009). ``None``
    when the stream carried no result record (a crashed rollout)."""

    records: tuple[DistilledLoopRecord, ...]
    session_id: str | None


def distill_stream(text: str, *, sidecar_dir: Path | None = None) -> StreamDistillation:
    """Distill stream-json stdout into ordered loop records (usage deduped).

    Example:
        >>> line = ('{"type":"assistant","message":{"id":"m1",'
        ...         '"content":[{"type":"text","text":"hi"}],'
        ...         '"usage":{"input_tokens":5}}}')
        >>> d = distill_stream(line)
        >>> d.records[0].usage
        {'input_tokens': 5}
    """
    records: list[DistilledLoopRecord] = []
    seen_message_ids: set[str] = set()
    turn = 0
    session_id: str | None = None
    for event in _iter_json_lines(text):
        etype = event.get("type")
        if etype == "assistant":
            turn = _distill_assistant(event, seen_message_ids, turn, records)
        elif etype == "user":
            records.extend(_distill_tool_results(event, turn, sidecar_dir))
        elif etype == "result":
            session_id = _string_or_none(event.get("session_id"))
            records.append(_distill_result(event, turn))
    return StreamDistillation(records=tuple(records), session_id=session_id)


def _iter_json_lines(text: str) -> Iterator[dict[str, Any]]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            decoded = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(decoded, dict):
            yield decoded


def _distill_assistant(
    event: dict[str, Any],
    seen_message_ids: set[str],
    turn: int,
    out: list[DistilledLoopRecord],
) -> int:
    """Append this assistant message's carrier + block records; return the turn.

    A new ``message.id`` bumps the turn and emits its usage exactly once — on a
    dedicated assistant-kind carrier record, never on a content block — so a
    message of only MCP tool_use blocks does not lose its usage when merge drops
    those blocks for the (usage-less) server tool event.
    """
    message = event.get("message")
    if not isinstance(message, dict):
        return turn
    message_id = _string_or_none(message.get("id"))
    is_new = message_id is not None and message_id not in seen_message_ids
    if is_new:
        seen_message_ids.add(message_id)  # type: ignore[arg-type]
        turn += 1
    usage = message.get("usage") if is_new else None
    out.extend(_usage_carrier(turn, message_id, usage))
    out.extend(_block_record(b, turn, message_id) for b in _content_blocks(message))
    return turn


def _usage_carrier(turn: int, message_id: str | None, usage: object) -> list[DistilledLoopRecord]:
    """The message's single usage-bearing assistant record, when usage is present.

    Emitting usage on its own assistant-kind record (not a content block) keeps it
    off the MCP ``tool_use`` records that merge discards, so per-message usage
    always reaches ``deduped_token_totals`` (ADR 0010)."""
    if not isinstance(usage, dict):
        return []
    return [DistilledLoopRecord(kind="assistant", turn=turn, message_id=message_id, usage=usage)]


def _content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _block_record(block: dict[str, Any], turn: int, message_id: str | None) -> DistilledLoopRecord:
    if block.get("type") == "tool_use":
        name = str(block.get("name", ""))
        return DistilledLoopRecord(
            kind="tool_use",
            turn=turn,
            message_id=message_id,
            tool=name,
            tool_input=_dict_or_none(block.get("input")),
            tool_use_id=_string_or_none(block.get("id")),
            is_mcp=name.startswith(_MCP_TOOL_PREFIX),
        )
    return DistilledLoopRecord(
        kind="assistant", turn=turn, message_id=message_id, text=_string_or_none(block.get("text"))
    )


def _distill_tool_results(
    event: dict[str, Any], turn: int, sidecar_dir: Path | None
) -> list[DistilledLoopRecord]:
    message = event.get("message")
    blocks = _content_blocks(message) if isinstance(message, dict) else []
    out: list[DistilledLoopRecord] = []
    for block in blocks:
        if block.get("type") != "tool_result":
            continue
        tool_use_id = _string_or_none(block.get("tool_use_id"))
        text = _resolve_tool_result_text(block, tool_use_id, sidecar_dir)
        out.append(
            DistilledLoopRecord(
                kind="tool_result",
                turn=turn,
                tool_use_id=tool_use_id,
                text=text,
                is_error=_bool_or_none(block.get("is_error")),
            )
        )
    return out


def _resolve_tool_result_text(
    block: dict[str, Any], tool_use_id: str | None, sidecar_dir: Path | None
) -> str | None:
    """Prefer the ``tool-results/<id>.txt`` sidecar over inline (spill-aware).

    The sidecar is authoritative for oversized results whose inline text the CLI
    elided; when it exists we read it, never trusting the inline body.
    """
    if sidecar_dir is not None and tool_use_id is not None:
        sidecar = sidecar_dir / f"{tool_use_id}.txt"
        if sidecar.exists():
            return sidecar.read_text(encoding="utf-8")
    return _tool_result_inline_text(block.get("content"))


def _tool_result_inline_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict) and "text" in b]
        return "".join(str(p) for p in parts if p is not None) or None
    return None


def _distill_result(event: dict[str, Any], turn: int) -> DistilledLoopRecord:
    return DistilledLoopRecord(
        kind="result",
        turn=turn,
        usage=_dict_or_none(event.get("usage")),
        text=_string_or_none(event.get("result")),
        is_error=_bool_or_none(event.get("is_error")),
    )


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _dict_or_none(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
