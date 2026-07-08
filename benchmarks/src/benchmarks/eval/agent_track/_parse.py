"""Pure parsers for the headless Claude CLI's two output shapes (spec §D15).

``parse_result_json`` reads the final ``--output-format json`` payload — the
answer text, total USD cost, and turn count. ``parse_stream_events`` folds the
``--output-format stream-json`` lines (one JSON object per line) into the
efficiency stats the paired report aggregates: total tool calls, MCP tool
calls counted separately, distinct files read, and summed cache tokens.

Both are pure and total: no I/O, no subprocess, and malformed stream lines are
skipped rather than fatal (a truncated run must still yield partial stats). The
real subprocess adapter (a later task) calls these; the Task-7 preflight
re-validates the REAL CLI still emits these fields, so fixture drift is caught
before any paid run — the parsers themselves stay hermetically testable.

CLI shape (documented in the plan's "Headless CLI contract"):
- result JSON: ``{"total_cost_usd", "num_turns", "result", ...}`` — cost may
  instead sit nested under a ``result`` object on some CLI versions, so the
  lookup tries both spellings.
- stream JSON line: ``{"type": "assistant", "message": {"content": [
  {"type": "tool_use", "name", "input"}, ...], "usage": {...}}}`` — usage
  blocks carry ``cache_read_input_tokens`` / ``cache_creation_input_tokens``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# The tool name prefix the Claude CLI stamps on every MCP-provided tool
# (``mcp__<server>__<tool>``). Single source of truth so a CLI rename is a
# one-line fix. Bare file tools (Read/Grep/Glob/Bash) carry no prefix.
_MCP_TOOL_PREFIX = "mcp__"
# The file-reading tool whose ``input.file_path`` feeds the distinct-files
# metric. Only reads count toward file access; MCP calls do not touch files.
_READ_TOOL = "Read"


@dataclass(frozen=True, slots=True)
class ParsedResult:
    """The scoring-relevant view of a final result JSON payload: the answer the
    judge scores, plus the two run-level facts (cost, turns) the report needs
    that the per-event stream does not authoritatively carry."""

    answer: str
    cost_usd: float
    turns: int


@dataclass(frozen=True, slots=True)
class StreamStats:
    """Efficiency stats folded from the per-event stream. ``tool_calls`` counts
    every ``tool_use`` event (MCP included); ``mcp_tool_calls`` is the MCP
    subset (broken out because the indexed arm's MCP usage is the thing being
    measured); ``distinct_files_read`` is the size of the set of ``Read`` file
    paths; cache token counts are summed across all usage blocks."""

    tool_calls: int
    mcp_tool_calls: int
    distinct_files_read: int
    cache_read_tokens: int
    cache_write_tokens: int


def parse_result_json(text: str) -> ParsedResult:
    """Parse the final ``--output-format json`` payload into a ``ParsedResult``.

    Cost lookup is tolerant: the CLI reports total spend under top-level
    ``total_cost_usd`` on current versions, but some versions nest it under a
    ``result`` object — both spellings are tried so a CLI shape shift degrades
    to a cost of 0.0 rather than a crash. ``num_turns`` and ``result`` (the
    answer text) follow the documented top-level shape.

    Example:
        >>> parse_result_json('{"total_cost_usd": 0.18, "num_turns": 3,'
        ...                    ' "result": "hi"}').cost_usd
        0.18
    """
    payload = json.loads(text)
    return ParsedResult(
        answer=str(payload.get("result", "")),
        cost_usd=_extract_cost(payload),
        turns=int(payload.get("num_turns", 0)),
    )


def _extract_cost(payload: dict[str, object]) -> float:
    # Try top-level first (current CLI), then a nested result-cost object
    # (older / alternate shape). Absent on both → 0.0, never a KeyError.
    top = payload.get("total_cost_usd")
    if top is not None:
        return float(top)  # type: ignore[arg-type]
    nested = payload.get("result")
    if isinstance(nested, dict):
        cost = nested.get("total_cost_usd", nested.get("cost_usd"))
        if cost is not None:
            return float(cost)  # type: ignore[arg-type]
    return 0.0


def parse_stream_events(text: str) -> StreamStats:
    """Fold ``--output-format stream-json`` lines into a ``StreamStats``.

    One JSON object per line; malformed lines (blank, truncated, non-JSON) are
    skipped so a partial run still yields partial stats. Tool calls are counted
    from ``tool_use`` content blocks (MCP names — ``mcp__…`` — also bump the
    ``mcp_tool_calls`` subtotal); distinct files come from ``Read`` inputs'
    ``file_path``; cache tokens are summed from every usage block.

    Example:
        >>> parse_stream_events('not json\\n').tool_calls
        0
    """
    tool_calls = 0
    mcp_tool_calls = 0
    files_read: set[str] = set()
    cache_read = 0
    cache_write = 0
    for event in _iter_events(text):
        c_read, c_write = _usage_tokens(event)
        cache_read += c_read
        cache_write += c_write
        for block in _tool_use_blocks(event):
            tool_calls += 1
            name = str(block.get("name", ""))
            if name.startswith(_MCP_TOOL_PREFIX):
                mcp_tool_calls += 1
            elif name == _READ_TOOL:
                _record_read(block, files_read)
    return StreamStats(
        tool_calls=tool_calls,
        mcp_tool_calls=mcp_tool_calls,
        distinct_files_read=len(files_read),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _iter_events(text: str):
    # Line-wise tolerant decode: skip blanks and any line json.loads can't
    # parse. A truncated stream must yield partial stats, not raise.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue


def _tool_use_blocks(event: object):
    # ``tool_use`` blocks live under ``message.content`` of assistant events.
    if not isinstance(event, dict):
        return
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _usage_tokens(event: object) -> tuple[int, int]:
    # Usage may sit on the event or under ``message`` depending on event kind;
    # check both. Missing counts contribute 0.
    if not isinstance(event, dict):
        return 0, 0
    usage = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return (
        int(usage.get("cache_read_input_tokens", 0)),
        int(usage.get("cache_creation_input_tokens", 0)),
    )


def _record_read(block: dict[str, object], files_read: set[str]) -> None:
    tool_input = block.get("input")
    if isinstance(tool_input, dict):
        path = tool_input.get("file_path")
        if isinstance(path, str) and path:
            files_read.add(path)
