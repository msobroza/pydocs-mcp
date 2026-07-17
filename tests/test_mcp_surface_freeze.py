"""MCP surface freeze — mechanical guard for G5 (AC23).

Feature PRs must change NOTHING about the nine task-shaped tools
(docs/tool-contracts.md §1): same tool list, same input-model shapes. A
failure here means a constitution-level versioning event snuck into a
feature PR.
"""

from __future__ import annotations

from typing import get_args

from pydocs_mcp.application.mcp_inputs import ReferencesInput
from pydocs_mcp.application.tool_docs import TOOL_DOCS


def test_the_nine_task_shaped_tools_are_unchanged() -> None:
    assert tuple(TOOL_DOCS) == (
        "get_overview",
        "search_codebase",
        "get_symbol",
        "get_context",
        "get_references",
        "get_why",
        "grep",
        "glob",
        "read_file",
    )


def test_references_input_shape_is_pinned() -> None:
    assert set(ReferencesInput.model_fields) == {"target", "direction", "project", "limit"}
    direction = ReferencesInput.model_fields["direction"].annotation
    assert set(get_args(direction)) == {
        "callers",
        "callees",
        "inherits",
        "impact",
        "governed_by",
    }
