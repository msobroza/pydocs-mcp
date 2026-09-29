"""MCP surface freeze — mechanical guard for G5 (AC23).

Feature PRs must change NOTHING about the nine task-shaped tools
(docs/tool-contracts.md §1): same tool list, same input-model shapes. A
failure here means a constitution-level versioning event snuck into a
feature PR.

The one sanctioned change so far is the ``branch`` corpus selector on all nine
tools — the contract amendment of ADR 0024 decision 1, carried by #315 and
ratified by merging it (contract §3, §5.2). It is pinned here like every other
field, so the next change is again a deliberate event.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GlobInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.tool_docs import TOOL_DOCS

_NINE_INPUT_MODELS: tuple[type[BaseModel], ...] = (
    OverviewInput,
    SearchInput,
    SymbolInput,
    ContextInput,
    ReferencesInput,
    WhyInput,
    GrepInput,
    GlobInput,
    ReadFileInput,
)


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
    assert set(ReferencesInput.model_fields) == {
        "target",
        "direction",
        "project",
        "limit",
        "branch",
    }
    direction = ReferencesInput.model_fields["direction"].annotation
    assert set(get_args(direction)) == {
        "callers",
        "callees",
        "inherits",
        "impact",
        "governed_by",
    }


def test_grep_input_shape_is_pinned() -> None:
    assert set(GrepInput.model_fields) == {
        "pattern",
        "path",
        "glob",
        "output_mode",
        "case_insensitive",
        "line_numbers",
        "after_context",
        "before_context",
        "context",
        "head_limit",
        "multiline",
        "scope",
        "project",
        "branch",
    }
    output_mode = GrepInput.model_fields["output_mode"].annotation
    assert set(get_args(output_mode)) == {"content", "files_with_matches", "count"}


@pytest.mark.parametrize("model", _NINE_INPUT_MODELS, ids=lambda m: m.__name__)
def test_every_input_model_carries_the_branch_selector(model: type[BaseModel]) -> None:
    """ADR 0024 decision 1: ``branch: str = ""`` on all nine tools, "" = the
    checked-out branch — so a call that omits it answers exactly as before."""
    field = model.model_fields["branch"]
    assert field.annotation is str and field.default == "" and not field.is_required()


def test_grep_dash_flag_wire_names_are_pinned() -> None:
    # Contract §3.7: the wire parameter names ARE the literal dash flags
    # (-i/-n/-A/-B/-C) — pydantic validation_alias maps them to python names.
    aliases = {
        name: field.validation_alias
        for name, field in GrepInput.model_fields.items()
        if field.validation_alias is not None
    }
    assert aliases == {
        "case_insensitive": "-i",
        "line_numbers": "-n",
        "after_context": "-A",
        "before_context": "-B",
        "context": "-C",
    }
