"""Structured envelope (docs/tool-contracts.md §2): dual text+structuredContent.

Every handler returns a ``CallToolResult`` whose text content block is
byte-identical to the pre-refactor string pipeline (golden-pinned below) and
whose ``structuredContent`` is the typed ``{text, items, meta}`` envelope,
validated against the per-tool pydantic model advertised as ``outputSchema``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.test_server import (
    _run_server_capture_tools,
    _seed_basic_fixture,
    _seed_tree_for_fastapi,
)

# ── goldens ────────────────────────────────────────────────────────────────
# Captured from the CURRENT six-tool str pipeline on the seeded fixtures
# (basic + fastapi.routing tree) BEFORE the ToolResponse refactor. The text
# content block must stay byte-identical across the 0.5.x → 0.6.0 boundary
# (contract §2, migration note 2).

GOLDEN: dict[str, str] = {
    "get_overview": (
        "# Overview — __project__\n"
        "[2 packages · 0 modules · 1 symbols · 100% documented]\n"
        "\n"
        "## Module map\n"
        "\n"
        "## Entry points\n"
        '- `pydocs-mcp` (script) → get_symbol(target="pydocs-mcp")\n'
        '- `ask-your-docs` (script) → get_symbol(target="ask-your-docs")\n'
        "\n"
        "## Structure communities\n"
        "Community structure is unavailable — enable reference_graph.node_scores to see it.\n"
        "\n"
        "## Dependency profile\n"
    ),
    "search_codebase": ("## Getting Started\nFastAPI is a modern web framework for APIs\n"),
    "get_symbol": (
        "{\n"
        '  "title": "class APIRouter",\n'
        '  "node_id": "fastapi.routing.APIRouter",\n'
        '  "kind": "class",\n'
        '  "source_path": "fastapi/routing.py",\n'
        '  "start_index": 10,\n'
        '  "end_index": 40,\n'
        '  "summary": "",\n'
        '  "nodes": [\n'
        "    {\n"
        '      "title": "def include_router",\n'
        '      "node_id": "fastapi.routing.APIRouter.include_router",\n'
        '      "kind": "method",\n'
        '      "source_path": "fastapi/routing.py",\n'
        '      "start_index": 20,\n'
        '      "end_index": 30,\n'
        '      "summary": "",\n'
        '      "nodes": []\n'
        "    }\n"
        "  ]\n"
        "}\n"
    ),
    "get_context": (
        "# Context for `fastapi.routing.APIRouter` — its dependency closure\n"
        "1 nodes in the closure (max depth 0). Skeleton fidelity: signatures for all, "
        "full source for the most-central.\n"
        "\n"
        "## `fastapi.routing.APIRouter`\n"
        "\n"
        "```python\n"
        "# (source unavailable)\n"
        "```\n"
    ),
    "get_references": ("# Callers of `fastapi.routing.APIRouter`\n\nNo callers found.\n"),
    "get_why": (
        "# Decision dashboard\n"
        "\n"
        "## By status\n"
        "\n"
        "## By source\n"
        "\n"
        "## Stalest active\n"
        "\n"
        "## Awaiting review\n"
        "\n"
        "## Ungoverned high-centrality modules\n"
    ),
}

_CALLS: dict[str, dict[str, object]] = {
    "get_overview": {"package": ""},
    "search_codebase": {"query": "framework", "kind": "docs"},
    "get_symbol": {"target": "fastapi.routing.APIRouter"},
    "get_context": {"targets": ["fastapi.routing.APIRouter"]},
    "get_references": {"target": "fastapi.routing.APIRouter", "direction": "callers"},
    "get_why": {},
}

_META_FIELDS = {
    "tool",
    "project",
    "indexed_git_head",
    "live_git_head",
    "index_stale",
    "truncated",
}


def _arun(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(scope="module")
def handlers(tmp_path_factory):
    db_path: Path = tmp_path_factory.mktemp("structured") / "structured.db"
    _seed_basic_fixture(db_path)
    _seed_tree_for_fastapi(db_path)
    return _run_server_capture_tools(db_path)


class _RecorderMCP:
    """Registration-only FastMCP double (no sys.modules patching — real mcp)."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **kwargs: object):
        def deco(fn):
            self.tools[str(kwargs["name"])] = fn
            return fn

        return deco


def _registered_with_real_mcp() -> dict[str, object]:
    from pydocs_mcp.server import _register_tools

    rec = _RecorderMCP()
    _register_tools(rec, tools=None)
    return rec.tools


# ── text content block: byte parity with the 0.5.x string pipeline ─────────


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_text_content_block_is_byte_identical_to_golden(handlers, tool: str) -> None:
    result = _arun(handlers[tool](**_CALLS[tool]))
    assert result.content[0].text == GOLDEN[tool]


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_structured_text_equals_content_block(handlers, tool: str) -> None:
    result = _arun(handlers[tool](**_CALLS[tool]))
    assert result.structuredContent["text"] == result.content[0].text


# ── structuredContent: {text, items, meta} envelope (contract §2.1) ────────


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_structured_envelope_shape(handlers, tool: str) -> None:
    sc = _arun(handlers[tool](**_CALLS[tool])).structuredContent
    assert set(sc) == {"text", "items", "meta"}
    # items[] are filled per-tool in later tasks; the envelope carries the
    # (empty) array today so the wire shape is already frozen.
    assert sc["items"] == []


@pytest.mark.parametrize("tool", sorted(_CALLS))
def test_structured_meta_contract_fields(handlers, tool: str) -> None:
    meta = _arun(handlers[tool](**_CALLS[tool])).structuredContent["meta"]
    expected = _META_FIELDS | ({"resolution"} if tool == "get_references" else set())
    assert set(meta) == expected
    assert meta["tool"] == tool
    # No project= selector sent -> the default (first-loaded) project's name,
    # derived from the {name}_{slug} db filename stem.
    assert meta["project"] == "structured"
    # The fixture never stamps index_metadata and lives outside a git checkout:
    # both heads are null and stale must NOT fire (§2.1 null semantics).
    assert meta["indexed_git_head"] is None
    assert meta["live_git_head"] is None
    assert meta["index_stale"] is False
    assert meta["truncated"] is False


def test_references_meta_reserves_resolution_slot(handlers) -> None:
    # §2.2 — the field exists on get_references only; the reference layer
    # stamps the declared capability value in a later task.
    meta = _arun(handlers["get_references"](**_CALLS["get_references"])).structuredContent["meta"]
    assert "resolution" in meta


# ── registration: per-tool outputSchema advertising (contract §2) ──────────


def test_registration_advertises_envelope_output_schema() -> None:
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    tools = _registered_with_real_mcp()
    assert set(tools) == set(_CALLS)
    for name, fn in tools.items():
        meta = func_metadata(fn)
        assert meta.output_schema is not None, f"{name} advertises no outputSchema"
        props = meta.output_schema.get("properties", {})
        assert {"text", "items", "meta"} <= set(props), f"{name} outputSchema drifted"


def test_convert_result_passes_call_tool_result_through(handlers) -> None:
    """FastMCP validates structuredContent against the advertised output model
    and passes the CallToolResult through unchanged (mcp 1.27.1 spike pin)."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    fn = _registered_with_real_mcp()["get_overview"]
    result = _arun(handlers["get_overview"](package=""))
    assert func_metadata(fn).convert_result(result) is result


# ── envelope model registry + ToolResponse value object ────────────────────


def test_envelope_model_registry_covers_all_nine_tools() -> None:
    from pydocs_mcp.application.tool_response import ENVELOPE_MODELS

    assert set(ENVELOPE_MODELS) == {
        "get_overview",
        "search_codebase",
        "get_symbol",
        "get_context",
        "get_references",
        "get_why",
        "grep",
        "glob",
        "read_file",
    }
    for model in ENVELOPE_MODELS.values():
        assert set(model.model_fields) == {"text", "items", "meta"}


def test_tool_response_structured_is_json_ready() -> None:
    from pydocs_mcp.application.tool_response import ToolResponse

    response = ToolResponse(text="t\n", items=({"path": "a.py"},), meta={"tool": "grep"})
    assert response.structured() == {
        "text": "t\n",
        "items": [{"path": "a.py"}],
        "meta": {"tool": "grep"},
    }
