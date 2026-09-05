"""Phase 2 Task 1 — distillation of every mcp 1.28.1 ``call_tool`` shape.

``FuncMetadata.convert_result`` (verified against the installed SDK) yields
one of: a ``CallToolResult`` passthrough, an ``(unstructured, structured)``
tuple, a structured dict, or unstructured content only.
"""

from __future__ import annotations

import json
from typing import Any

from pydocs_mcp.observability.result_distiller import distill_tool_result

_ENVELOPE: dict[str, Any] = {
    "text": "## hits\n",
    "items": [{"id": "c1", "qualified_name": "pkg.fn", "score": 1.0}],
    "meta": {"tool": "search_codebase", "project": "p", "truncated": False},
}


class _DuckCallToolResult:
    def __init__(self, structured: dict[str, Any] | None) -> None:
        self.structuredContent = structured
        self.content: list[Any] = []


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


def test_call_tool_result_shape_distills_envelope() -> None:
    distilled = distill_tool_result(_DuckCallToolResult(_ENVELOPE))
    assert distilled.hit_count == 1
    assert distilled.truncated is False
    assert distilled.suggestion is None
    assert distilled.result_ids == ({"id": "c1", "qualified_name": "pkg.fn"},)
    assert json.loads(distilled.serialized) == _ENVELOPE


def test_unstructured_structured_tuple_shape() -> None:
    """``convert_result`` returns ``(content_blocks, structured_dict)`` for
    plain-return tools with an output schema."""
    distilled = distill_tool_result(([_TextBlock("hi")], {"result": "hi"}))
    assert distilled.hit_count is None  # not an envelope — nothing fabricated
    assert json.loads(distilled.serialized) == {"result": "hi"}


def test_plain_structured_dict_shape() -> None:
    distilled = distill_tool_result(dict(_ENVELOPE))
    assert distilled.hit_count == 1


def test_unstructured_only_shape_preserves_text() -> None:
    distilled = distill_tool_result([_TextBlock("alpha"), _TextBlock("beta")])
    assert distilled.result_ids is None
    assert json.loads(distilled.serialized) == {"unstructured_text": ["alpha", "beta"]}


def test_call_tool_result_without_structured_content_falls_back_to_content() -> None:
    result = _DuckCallToolResult(None)
    result.content = [_TextBlock("only text")]
    distilled = distill_tool_result(result)
    assert json.loads(distilled.serialized) == {"unstructured_text": ["only text"]}


def test_scalar_result_is_captured_via_repr() -> None:
    distilled = distill_tool_result(42)
    assert json.loads(distilled.serialized) == {"unstructured_repr": "42"}


def test_empty_items_envelope_yields_zero_hit_count() -> None:
    envelope = {"text": "none", "items": [], "meta": {"truncated": True}}
    distilled = distill_tool_result(_DuckCallToolResult(envelope))
    assert distilled.hit_count == 0
    assert distilled.truncated is True
    assert distilled.result_ids == ()
