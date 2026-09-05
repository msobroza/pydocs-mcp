"""Distill one ``FastMCP.call_tool`` return value into trace-event fields.

Verified against the installed mcp 1.28.1 source: ``FastMCP.call_tool``
delegates to ``ToolManager.call_tool(..., convert_result=True)`` whose
``FuncMetadata.convert_result`` returns (a) a ``CallToolResult`` unchanged
when the handler returned one — this repo's nine tools all do, carrying the
``{"text", "items", "meta"}`` envelope in ``structuredContent`` — or (b) an
``(unstructured_content, structured_dict)`` tuple, or (c) unstructured
content only. All three shapes are handled here by duck typing (``getattr``,
never ``isinstance``) so the module stays stdlib-only.

Schema semantics (ADR 0010): ``hit_count`` is derived ``len(items)`` — meta
carries only booleans, no numeric totals exist; ``result_ids`` presence must
NOT be read as "shown to the model" (items can exceed the token-budgeted
text and grep's per-file modes leak content the text omits) — model-visible
surfacing is judged from the text side, dereferenced from the blob.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.observability.trace_writer import canonical_trace_json

# The per-item identifier atoms as the tools emit them (ADR 0010): path /
# line span / qualified name / chunk-or-node id. Fixed order for R6.
_RESULT_ID_KEYS = ("id", "node_id", "qualified_name", "path", "start_line", "end_line")

# The response-envelope keys every task-shaped tool emits (tool-contracts
# §2.1); a structured payload without them is opaque — derived fields would
# be fabrications, not distillations.
_ENVELOPE_KEYS = frozenset({"text", "items", "meta"})


@dataclass(frozen=True, slots=True)
class DistilledToolResult:
    """The event-line result fields plus the full serialized payload."""

    serialized: bytes
    result_ids: tuple[dict[str, Any], ...] | None
    hit_count: int | None
    truncated: bool | None
    suggestion: str | None


def distill_tool_result(result: object) -> DistilledToolResult:
    """Distill any ``call_tool`` return shape into trace-event fields.

    Example:
        >>> envelope = {"text": "hi", "items": [], "meta": {"truncated": False}}
        >>> class R:  # duck-typed CallToolResult
        ...     structuredContent = envelope
        >>> distill_tool_result(R()).hit_count
        0
    """
    payload = _structured_payload(result)
    if payload is None:
        return _opaque(_serialize(_fallback_payload(result)))
    serialized = _serialize(payload)
    if not payload.keys() >= _ENVELOPE_KEYS:
        return _opaque(serialized)
    items = payload["items"] or []
    meta = payload["meta"] or {}
    return DistilledToolResult(
        serialized=serialized,
        result_ids=tuple(_identifier_atoms(item) for item in items),
        hit_count=len(items),
        truncated=bool(meta.get("truncated", False)),
        suggestion=meta.get("suggestion"),
    )


def _structured_payload(result: object) -> dict[str, Any] | None:
    """The structured dict of any of the three convert_result shapes."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, dict):
        return result
    return None


def _fallback_payload(result: object) -> dict[str, Any]:
    """Unstructured-only results: preserve the text the client would see."""
    content = getattr(result, "content", result)
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return {"unstructured_text": [getattr(block, "text", repr(block)) for block in content]}
    return {"unstructured_repr": repr(result)}


def _identifier_atoms(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in _RESULT_ID_KEYS if item.get(key) is not None}


def _serialize(payload: dict[str, Any]) -> bytes:
    return canonical_trace_json(payload).encode("utf-8")


def _opaque(serialized: bytes) -> DistilledToolResult:
    return DistilledToolResult(
        serialized=serialized, result_ids=None, hit_count=None, truncated=None, suggestion=None
    )
