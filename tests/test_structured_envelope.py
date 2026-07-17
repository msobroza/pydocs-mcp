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

# Every tool emits items[] now; the golden calls hit empty corpora for the
# reference/decision layers, so these two arrays stay empty ON THIS FIXTURE
# (zero rows ≠ pending — the shape tests below assert filled rows).
_ITEMS_EMPTY_ON_GOLDEN_FIXTURE = {"get_references", "get_why"}


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
    assert isinstance(sc["items"], list)
    if tool in _ITEMS_EMPTY_ON_GOLDEN_FIXTURE:
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


def test_references_meta_carries_declared_resolution(handlers) -> None:
    # §2.2 — get_references only; the value is the Python analyzer's declared
    # capability flag (single source: PYTHON_CAPABILITIES["references"]).
    from pydocs_mcp.extraction.strategies.analyzers import PYTHON_CAPABILITIES

    meta = _arun(handlers["get_references"](**_CALLS["get_references"])).structuredContent["meta"]
    assert meta["resolution"] == "syntactic"
    assert meta["resolution"] == PYTHON_CAPABILITIES["references"]


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


# ── items[]: search_codebase + get_overview (contract §3.1/§3.2, Task 5) ────


def _seed_items_fixture(db_path: Path) -> None:
    """Rows the items[] tests need beyond the basic seed: a chunk WITH the
    schema-v15 source span, a member whose (package, module) tree resolves its
    span, and one mined decision (record + decision-record chunk backlink)."""
    from pydocs_mcp.db import open_index_database, rebuild_fulltext_index

    conn = open_index_database(db_path)
    conn.execute(
        "INSERT INTO chunks("
        "package,module,title,text,origin,qualified_name,source_path,start_line,end_line"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "fastapi",
            "fastapi.spanmod",
            "Span Doc",
            "searchable spanprobe text",
            "dependency_doc_file",
            "fastapi.spanmod.SpanDoc",
            "fastapi/spanmod.py",
            5,
            9,
        ),
    )
    conn.execute(
        "INSERT INTO module_members("
        "package,module,name,kind,signature,return_annotation,parameters,docstring"
        ") VALUES(?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi.routing", "APIRouter", "class", "()", "", "[]", "Router class"),
    )
    conn.execute(
        "INSERT INTO decision_records("
        "package,title,status,source,confidence,evidence,affected_files,affected_qnames,"
        "staleness_score,superseded_by,verification,structured,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "__project__",
            "Adopt sidecar vectors",
            "active",
            "adr_files",
            0.9,
            '[{"source": "adr_files", "locator": "docs/adr/0001.md:1-40",'
            ' "text": "We adopt sidecar vectors"}]',
            '["python/pydocs_mcp/storage/turboquant.py"]',
            "[]",
            0.0,
            None,
            "verbatim",
            None,
            0.0,
            0.0,
        ),
    )
    # Reference edges for the §3.5 items tests: one resolved call INTO
    # APIRouter (its caller's defining node lives in the seeded tree), one
    # resolved + one unresolved callee OUT of include_router, and two GOVERNS
    # projections (symbol-level for direction="governed_by"; module-level for
    # the get_why targets mode).
    from pydocs_mcp.extraction.decisions.engine import decision_key

    decision_node = f"decision:{decision_key('Adopt sidecar vectors')}"
    for row in (
        (
            "fastapi",
            "fastapi.routing.APIRouter.include_router",
            "APIRouter",
            "fastapi.routing.APIRouter",
            "calls",
        ),
        (
            "fastapi",
            "fastapi.routing.APIRouter.include_router",
            "starlette.routing.Mount",
            None,
            "calls",
        ),
        (
            "__project__",
            decision_node,
            "fastapi.routing.APIRouter.include_router",
            "fastapi.routing.APIRouter.include_router",
            "governs",
        ),
        ("__project__", decision_node, "mymod", "mymod", "governs"),
    ):
        conn.execute(
            "INSERT INTO node_references(from_package,from_node_id,to_name,to_node_id,kind)"
            " VALUES(?,?,?,?,?)",
            row,
        )
    conn.execute(
        "INSERT INTO chunks(package,module,title,text,origin,decision_id) VALUES(?,?,?,?,?,?)",
        (
            "__project__",
            "",
            "Adopt sidecar vectors",
            "decision sidecar vectors rationale",
            "decision_record",
            1,
        ),
    )
    conn.commit()
    rebuild_fulltext_index(conn)
    conn.close()


@pytest.fixture(scope="module")
def items_handlers(tmp_path_factory):
    db_path: Path = tmp_path_factory.mktemp("items") / "items.db"
    _seed_basic_fixture(db_path)
    _seed_tree_for_fastapi(db_path)
    _seed_items_fixture(db_path)
    return _run_server_capture_tools(db_path)


_SEARCH_ITEM_FIELDS = {
    "kind",
    "id",
    "qualified_name",
    "package",
    "path",
    "start_line",
    "end_line",
    "score",
}


def test_search_items_chunk_row_carries_source_span(items_handlers) -> None:
    sc = _arun(items_handlers["search_codebase"](query="spanprobe", kind="docs")).structuredContent
    rows = [i for i in sc["items"] if i["qualified_name"] == "fastapi.spanmod.SpanDoc"]
    assert rows, sc["items"]
    row = rows[0]
    assert set(row) == _SEARCH_ITEM_FIELDS
    assert row["kind"] == "chunk"
    assert row["package"] == "fastapi"
    assert row["path"] == "fastapi/spanmod.py"
    assert (row["start_line"], row["end_line"]) == (5, 9)
    assert isinstance(row["score"], float)
    assert row["id"].isdigit()


def test_search_items_member_row_resolves_span_from_tree(items_handlers) -> None:
    sc = _arun(items_handlers["search_codebase"](query="APIRouter", kind="api")).structuredContent
    row = next(i for i in sc["items"] if i["qualified_name"] == "fastapi.routing.APIRouter")
    assert set(row) == _SEARCH_ITEM_FIELDS
    assert row["kind"] == "member"
    assert row["package"] == "fastapi"
    assert row["path"] == "fastapi/routing.py"
    assert (row["start_line"], row["end_line"]) == (10, 40)


def test_search_items_member_row_without_tree_has_null_span(items_handlers) -> None:
    sc = _arun(items_handlers["search_codebase"](query="compute", kind="api")).structuredContent
    row = next(i for i in sc["items"] if i["qualified_name"] == "mymod.compute")
    assert row["path"] is None
    assert row["start_line"] is None and row["end_line"] is None


def test_search_items_decision_row_keeps_locators_in_get_why(items_handlers) -> None:
    sc = _arun(
        items_handlers["search_codebase"](query="sidecar", kind="decision")
    ).structuredContent
    row = next(i for i in sc["items"] if i["kind"] == "decision")
    assert set(row) == _SEARCH_ITEM_FIELDS
    assert row["id"] == "1"
    assert row["package"] == "__project__"
    assert row["path"] is None
    assert row["start_line"] is None and row["end_line"] is None
    assert isinstance(row["score"], float)


# ── items[]: get_symbol + get_context (contract §3.3/§3.4, Task 6) ──────────

# The §3.3 rows for the seeded fastapi.routing.APIRouter node — CONTRACT
# names (path/start_line/end_line), not the pageindex keys the text body
# renders (source_path/start_index/end_index).
_SYMBOL_OUTLINE_ITEMS = [
    {
        "node_id": "fastapi.routing.APIRouter",
        "kind": "class",
        "qualified_name": "fastapi.routing.APIRouter",
        "path": "fastapi/routing.py",
        "start_line": 10,
        "end_line": 40,
    },
    {
        "node_id": "fastapi.routing.APIRouter.include_router",
        "kind": "method",
        "qualified_name": "fastapi.routing.APIRouter.include_router",
        "path": "fastapi/routing.py",
        "start_line": 20,
        "end_line": 30,
    },
]


def test_symbol_items_mirror_rendered_outline(handlers) -> None:
    sc = _arun(handlers["get_symbol"](target="fastapi.routing.APIRouter")).structuredContent
    assert sc["items"] == _SYMBOL_OUTLINE_ITEMS


def test_symbol_tree_depth_emits_same_outline_rows(handlers) -> None:
    # summary and tree render the same pageindex payload today; the rows
    # mirror whatever outline the text carries.
    sc = _arun(
        handlers["get_symbol"](target="fastapi.routing.APIRouter", depth="tree")
    ).structuredContent
    assert sc["items"] == _SYMBOL_OUTLINE_ITEMS


def test_symbol_module_target_items_lead_with_module_row(handlers) -> None:
    sc = _arun(handlers["get_symbol"](target="fastapi.routing")).structuredContent
    assert sc["items"][0] == {
        "node_id": "fastapi.routing",
        "kind": "module",
        "qualified_name": "fastapi.routing",
        "path": "fastapi/routing.py",
        "start_line": 1,
        "end_line": 50,
    }
    # Descendants follow in the pageindex pre-order.
    assert [i["node_id"] for i in sc["items"][1:]] == [
        "fastapi.routing.APIRouter",
        "fastapi.routing.APIRouter.include_router",
    ]


def test_symbol_package_target_emits_no_items(handlers) -> None:
    # Package overview card — no document-tree nodes to attribute.
    sc = _arun(handlers["get_symbol"](target="fastapi")).structuredContent
    assert sc["items"] == []


def test_symbol_source_depth_emits_one_span_item(items_handlers) -> None:
    # The rendered verbatim span is the single §3.3 row; chunks don't persist
    # a node kind, so ``kind`` degrades to "" on this path.
    sc = _arun(
        items_handlers["get_symbol"](target="fastapi.spanmod.SpanDoc", depth="source")
    ).structuredContent
    assert sc["items"] == [
        {
            "node_id": "fastapi.spanmod.SpanDoc",
            "kind": "",
            "qualified_name": "fastapi.spanmod.SpanDoc",
            "path": "fastapi/spanmod.py",
            "start_line": 5,
            "end_line": 9,
        }
    ]


def test_context_items_one_row_per_resolved_target(handlers) -> None:
    sc = _arun(handlers["get_context"](targets=["fastapi.routing.APIRouter"])).structuredContent
    assert sc["items"] == [
        {
            "qualified_name": "fastapi.routing.APIRouter",
            "kind": "class",
            "path": "fastapi/routing.py",
            "start_line": 10,
            "end_line": 40,
        }
    ]


def test_overview_items_carry_module_map_rows(items_handlers) -> None:
    sc = _arun(items_handlers["get_overview"](package="fastapi")).structuredContent
    assert sc["items"] == [
        {
            "kind": "module",
            "id": "fastapi.routing",
            "qualified_name": "fastapi.routing",
            "path": "fastapi/routing.py",
        }
    ]


# ── items[]: get_references + get_why (contract §3.5/§3.6, Task 8) ──────────

_REFERENCE_ITEM_FIELDS = {
    "from_qualified_name",
    "to_qualified_name",
    "kind",
    "direction",
    "path",
    "start_line",
    "end_line",
}

_WHY_ITEM_FIELDS = {"decision_id", "title", "status", "locators", "affected_files"}

_WHY_ROW = {
    "decision_id": 1,
    "title": "Adopt sidecar vectors",
    "status": "active",
    "locators": ["docs/adr/0001.md:1-40"],
    "affected_files": ["python/pydocs_mcp/storage/turboquant.py"],
}


def test_reference_items_callers_span_from_from_node(items_handlers) -> None:
    # callers ⇒ path/span of the FROM-node's defining tree node (§3.5).
    sc = _arun(
        items_handlers["get_references"](target="fastapi.routing.APIRouter", direction="callers")
    ).structuredContent
    assert sc["items"] == [
        {
            "from_qualified_name": "fastapi.routing.APIRouter.include_router",
            "to_qualified_name": "fastapi.routing.APIRouter",
            "kind": "calls",
            "direction": "callers",
            "path": "fastapi/routing.py",
            "start_line": 20,
            "end_line": 30,
        }
    ]


def test_reference_items_callees_resolved_and_unresolved(items_handlers) -> None:
    # callees ⇒ path/span of the TO-node's defining node; unresolved edges
    # carry to_name and a null span (§3.5 "null on miss").
    sc = _arun(
        items_handlers["get_references"](
            target="fastapi.routing.APIRouter.include_router", direction="callees"
        )
    ).structuredContent
    rows = {row["to_qualified_name"]: row for row in sc["items"]}
    assert set(rows) == {"fastapi.routing.APIRouter", "starlette.routing.Mount"}
    resolved = rows["fastapi.routing.APIRouter"]
    assert set(resolved) == _REFERENCE_ITEM_FIELDS
    assert resolved["direction"] == "callees"
    assert resolved["path"] == "fastapi/routing.py"
    assert (resolved["start_line"], resolved["end_line"]) == (10, 40)
    unresolved = rows["starlette.routing.Mount"]
    assert unresolved["path"] is None
    assert unresolved["start_line"] is None and unresolved["end_line"] is None


def test_reference_items_governed_by_decision_node_has_null_span(items_handlers) -> None:
    # governed_by rows originate from a synthetic decision:<key> node — no
    # defining tree node, so the span degrades to null.
    sc = _arun(
        items_handlers["get_references"](
            target="fastapi.routing.APIRouter.include_router", direction="governed_by"
        )
    ).structuredContent
    assert len(sc["items"]) == 1
    row = sc["items"][0]
    assert row["from_qualified_name"].startswith("decision:")
    assert row["to_qualified_name"] == "fastapi.routing.APIRouter.include_router"
    assert row["kind"] == "governs"
    assert row["direction"] == "governed_by"
    assert row["path"] is None
    assert row["start_line"] is None and row["end_line"] is None


def test_reference_items_impact_direction_stays_empty(items_handlers) -> None:
    # direction="impact" renders ranked blast-radius NODES, not graph edges —
    # the §3.5 edge rows don't apply; the array stays empty by design.
    sc = _arun(
        items_handlers["get_references"](target="fastapi.routing.APIRouter", direction="impact")
    ).structuredContent
    assert sc["items"] == []


def test_why_items_query_mode(items_handlers) -> None:
    sc = _arun(items_handlers["get_why"](query="sidecar vectors")).structuredContent
    assert sc["items"] == [_WHY_ROW]
    assert set(sc["items"][0]) == _WHY_ITEM_FIELDS


def test_why_items_targets_mode(items_handlers) -> None:
    # GOVERNS-edge-backed target mode (§D18) emits the governing record's row.
    sc = _arun(items_handlers["get_why"](targets=["mymod"])).structuredContent
    assert sc["items"] == [_WHY_ROW]


def test_why_items_dashboard_mode_lists_surfaced_records(items_handlers) -> None:
    # Dashboard mode surfaces the stalest/awaiting records — its items[] carry
    # the same §3.6 rows so a harness can attribute what the rollup showed.
    sc = _arun(items_handlers["get_why"]()).structuredContent
    assert sc["items"] == [_WHY_ROW]
