"""The eval merge folds ``kind="api"`` zero-hit fallback traces (ADR 0027, ADR 0009 / 0010).

The paid before/after report counts raw API zero hits, served fallback rows
and gold at rank inside them from the merged tool events, without redefining
a counter. This module proves that the merge accepts every fallback outcome
and keeps them apart.

Real bytes, not hand-written JSON (the ``_ask_traces`` precedent): each
trajectory is served by a real product ``ToolRouter`` over the named fakes
below, registered by the server's own tool registration on a real
``TracingToolServer``. Its ``TraceRecorder`` writes ``server_events.jsonl``,
and the eval's ``group_fired_rules`` + ``build_tool_event`` fold it. This
suite cannot import the product suite's test package, so its fakes are
local. The label texts, the rule name and the failure prefix come from the
product's constants.

Two arms, the same five calls each:

- AFTER: the fallback flag on (the default);
- BEFORE-shaped: the flag off. Ticket 01 pins that path byte-identical to the
  pre-fallback build, so its trace stands in for a BEFORE trace here.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest

from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.schema import SCHEMA_VERSION, ToolEvent
from pydocs_eval.trajectory.server_capture import (
    SERVER_EVENTS_FILENAME,
    assert_schema_version,
    build_tool_event,
    group_fired_rules,
    read_server_capture,
)
from pydocs_mcp.application.api_zero_hit_fallback import API_ZERO_HIT_FALLBACK_FAILURE_PREFIX
from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.freshness import NullFreshnessProbe
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.suggestions import (
    API_ZERO_HIT_FALLBACK_ANY_RULE,
    API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import (
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
    SearchResponse,
)
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.tracing_server import TracingToolServer
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.server import _register_tools
from pydocs_mcp.storage.index_metadata import IndexMetadata

TOOL = "search_codebase"
HIT_QUERY = "serialize"  # names a top-level public function: an API hit
SERVED_QUERY = "nested serialization"  # only a method's chunk matches it
EMPTY_QUERY = "a phrase nothing in the corpus holds"
FAILING_QUERY = "a phrase whose dense path is down"

# The five calls of each arm, in seq order.
CALLS = (
    {"query": HIT_QUERY, "kind": "api"},  # seq 1: H
    {"query": SERVED_QUERY, "kind": "api"},  # seq 2: S under the fallback
    {"query": EMPTY_QUERY, "kind": "api"},  # seq 3: E under the fallback
    {"query": FAILING_QUERY, "kind": "api"},  # seq 4: X under the fallback
    {"query": SERVED_QUERY, "kind": "any"},  # seq 5: the direct kind="any" control
)
H_SEQ, S_SEQ, E_SEQ, X_SEQ, ANY_SEQ = 1, 2, 3, 4, 5


def _method_chunk(chunk_id: int, title: str, start: int, end: int) -> Chunk:
    return Chunk(
        text=f"{title}: nested serialization walks every child node.",
        id=chunk_id,
        relevance=0.9,
        metadata={
            "title": title,
            "qualified_name": f"pkg.mod.{title}",
            "package": "__project__",
            "source_path": "pkg/mod.py",
            "start_line": start,
            "end_line": end,
        },
    )


SERVED_ROWS = (_method_chunk(11, "Tree.walk", 3, 9), _method_chunk(12, "Tree.dump", 12, 20))
SERIALIZE_MEMBER = ModuleMember(
    id=7,
    relevance=0.5,
    metadata={
        "package": "__project__",
        "module": "pkg.mod",
        "name": "serialize",
        "kind": "function",
        "signature": "(tree)",
        "docstring": "Serialize a tree.",
    },
)


class QueryScriptedDocsSearch:
    """A docs search answering each query text with its scripted chunk rows,
    or raising its scripted error (a dense path that is down); any other
    query finds nothing."""

    def __init__(self, script: Mapping[str, tuple[Chunk, ...] | Exception]) -> None:
        self.script = script

    async def search(self, query: SearchQuery) -> SearchResponse:
        return SearchResponse(result=await self.ranked(query), query=query, duration_ms=0.0)

    async def ranked(self, query: SearchQuery) -> ChunkList:
        answer = self.script.get(query.terms, ())
        if isinstance(answer, Exception):
            raise answer
        return ChunkList(items=answer)


class QueryScriptedApiSearch:
    """The member-search twin of :class:`QueryScriptedDocsSearch`."""

    def __init__(self, script: Mapping[str, tuple[ModuleMember, ...]]) -> None:
        self.script = script

    async def search(self, query: SearchQuery) -> SearchResponse:
        return SearchResponse(result=await self.ranked(query), query=query, duration_ms=0.0)

    async def ranked(self, query: SearchQuery) -> ModuleMemberList:
        return ModuleMemberList(items=self.script.get(query.terms, ()))


class ServiceTheSearchPathNeverCalls:
    """Stands in for the lookup, source and overview services a
    ``search_codebase`` call never reaches; it carries no tree navigator, the
    shape the search body already degrades on."""


def _project_services() -> ProjectServices:
    docs = QueryScriptedDocsSearch(
        {SERVED_QUERY: SERVED_ROWS, FAILING_QUERY: RuntimeError("embedder down")}
    )
    unused = ServiceTheSearchPathNeverCalls()
    metadata = IndexMetadata(
        project_name="solo",
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=0.0,
    )
    return ProjectServices(
        project=LoadedProject(name="solo", db_path=Path("/x/solo.db"), metadata=metadata),
        docs=docs,
        api=QueryScriptedApiSearch({HIT_QUERY: (SERIALIZE_MEMBER,)}),
        lookup=unused,
        symbol_source=unused,
        overview=unused,
        decisions=NullDecisionService(),
    )


def _search_router(suggestions: SuggestionsConfig) -> ToolRouter:
    services = (_project_services(),)
    return ToolRouter(
        services=services,
        envelope=ResponseEnvelope(probe=NullFreshnessProbe(), surface="mcp", pointers_enabled=True),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions,
    )


async def _dispatch(server: TracingToolServer, arguments: dict[str, Any]) -> None:
    handler = server._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=TOOL, arguments=arguments),
    )
    await handler(request)


async def record_arm(trace_root: Path, *, fallback_on: bool) -> Path:
    """Serve :data:`CALLS` through one traced server; return its trajectory dir."""
    trajectory_id = "after" if fallback_on else "before"
    recorder = TraceRecorder(trace_dir=trace_root, trajectory_id=trajectory_id)
    recorder.open_trace()
    server = TracingToolServer("pydocs-mcp", recorder=recorder)
    _register_tools(
        server, _search_router(SuggestionsConfig(api_zero_hit_fallback_any=fallback_on))
    )
    try:
        for arguments in CALLS:
            await _dispatch(server, dict(arguments))
    finally:
        recorder.close()
    return trace_root / trajectory_id


def merge_server_events(trajectory_dir: Path) -> dict[int, ToolEvent]:
    """Fold one raw capture into canonical tool events, keyed by seq — the
    shared reading every eval path runs (each call its own turn here)."""
    capture = read_server_capture(trajectory_dir / SERVER_EVENTS_FILENAME)
    assert_schema_version(capture.header)
    fired_by_seq = group_fired_rules(capture.fired_records, capture.tool_events)
    trajectory_id = str(capture.header["trajectory_id"])
    events = (
        build_tool_event(
            raw, turn=raw["seq"], fired_by_seq=fired_by_seq, trajectory_id=trajectory_id
        )
        for raw in capture.tool_events
    )
    return {event.seq: event for event in events}


@pytest.fixture
async def after_arm(tmp_path: Path) -> dict[int, ToolEvent]:
    return merge_server_events(await record_arm(tmp_path, fallback_on=True))


@pytest.fixture
async def before_arm(tmp_path: Path) -> dict[int, ToolEvent]:
    return merge_server_events(await record_arm(tmp_path, fallback_on=False))


def rules_of(event: ToolEvent) -> list[str | None]:
    return [fired.rule for fired in event.fired_rules]


# ── The AFTER arm merges, outcome by outcome ──────────────────────────────


def test_every_call_of_the_fallback_arm_merges_without_a_cross_check_error(
    after_arm: dict[int, ToolEvent],
) -> None:
    assert sorted(after_arm) == [H_SEQ, S_SEQ, E_SEQ, X_SEQ, ANY_SEQ]
    assert [after_arm[seq].args for seq in sorted(after_arm)] == list(CALLS)


def test_merged_served_call_carries_the_fallback_rule_and_the_served_rows(
    after_arm: dict[int, ToolEvent],
) -> None:
    served, direct_any = after_arm[S_SEQ], after_arm[ANY_SEQ]
    assert served.args["kind"] == "api"
    assert rules_of(served) == [API_ZERO_HIT_FALLBACK_ANY_RULE]
    assert served.suggestion == API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    assert served.error is None
    assert served.hit_count == 2 and served.rendered_rows == 2
    # Gold at rank is read off the served rows, in served order.
    assert [atom["qualified_name"] for atom in served.result_ids or ()] == [
        "pkg.mod.Tree.walk",
        "pkg.mod.Tree.dump",
    ]
    assert served.result_ids == direct_any.result_ids
    assert served.rendered_rows == direct_any.rendered_rows


def test_merged_empty_call_carries_both_rules_in_order_and_no_rows(
    after_arm: dict[int, ToolEvent],
) -> None:
    empty = after_arm[E_SEQ]
    assert empty.args["kind"] == "api"
    assert rules_of(empty) == ["search_zero_hit", API_ZERO_HIT_FALLBACK_ANY_RULE]
    assert empty.suggestion == API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
    assert empty.hit_count == 0 and empty.rendered_rows == 0 and empty.result_ids == ()


def test_merged_failed_call_is_a_typed_error_with_no_fired_rule(
    after_arm: dict[int, ToolEvent],
) -> None:
    failed = after_arm[X_SEQ]
    assert failed.args["kind"] == "api"
    assert failed.fired_rules == () and failed.suggestion is None
    assert failed.hit_count is None and failed.rendered_rows is None
    error = failed.error or {}
    # FastMCP's ToolError wraps the typed error on the traced dispatch path.
    assert (error.get("type"), error.get("cause")) == ("ToolError", "ServiceUnavailableError")
    assert (
        f"{API_ZERO_HIT_FALLBACK_FAILURE_PREFIX}: RuntimeError: embedder down" in error["message"]
    )


def test_merged_hit_and_direct_any_calls_carry_no_fallback_rule(
    after_arm: dict[int, ToolEvent],
) -> None:
    hit, direct_any = after_arm[H_SEQ], after_arm[ANY_SEQ]
    assert rules_of(hit) == [] and hit.suggestion is None and hit.hit_count == 1
    assert rules_of(direct_any) == [] and direct_any.suggestion is None
    assert direct_any.hit_count == 2


# ── The BEFORE-shaped arm: today's events, none of the new markers ────────


def test_flag_off_arm_merges_to_todays_zero_hit_events(
    before_arm: dict[int, ToolEvent],
) -> None:
    for seq in (S_SEQ, E_SEQ, X_SEQ):
        zero_hit = before_arm[seq]
        assert rules_of(zero_hit) == ["search_zero_hit", "api_zero_hit_retry_any"]
        assert API_ZERO_HIT_FALLBACK_ANY_RULE not in rules_of(zero_hit)
        assert zero_hit.suggestion not in (
            API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION,
            API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
        )
        assert zero_hit.error is None and zero_hit.hit_count == 0


# ── One counting rule reads both arms (the report's raw API zero hit) ─────


def is_requested_api_call(event: ToolEvent) -> bool:
    return event.tool == TOOL and event.args.get("kind") == "api"


def is_raw_api_zero_hit(event: ToolEvent) -> bool:
    """The report's definition: a requested-API call on which the fallback
    rule fired, or the fallback failed, or (neither) nothing was returned."""
    if not is_requested_api_call(event):
        return False
    fallback_fired = API_ZERO_HIT_FALLBACK_ANY_RULE in rules_of(event)
    message = str((event.error or {}).get("message", ""))
    fallback_failed = API_ZERO_HIT_FALLBACK_FAILURE_PREFIX in message
    return fallback_fired or fallback_failed or (event.error is None and event.hit_count == 0)


def test_raw_api_zero_hits_count_the_same_in_both_arms(
    after_arm: dict[int, ToolEvent], before_arm: dict[int, ToolEvent]
) -> None:
    expected = {H_SEQ: False, S_SEQ: True, E_SEQ: True, X_SEQ: True, ANY_SEQ: False}
    assert {seq: is_raw_api_zero_hit(e) for seq, e in after_arm.items()} == expected
    assert {seq: is_raw_api_zero_hit(e) for seq, e in before_arm.items()} == expected


def test_on_the_flag_off_arm_a_raw_api_zero_hit_is_exactly_hits_zero(
    before_arm: dict[int, ToolEvent],
) -> None:
    for event in before_arm.values():
        hits_zero = is_requested_api_call(event) and event.hit_count == 0
        assert is_raw_api_zero_hit(event) is hits_zero


def test_served_fallback_rows_are_counted_apart_from_the_zero_hits(
    after_arm: dict[int, ToolEvent],
) -> None:
    served = [
        e
        for e in after_arm.values()
        if API_ZERO_HIT_FALLBACK_ANY_RULE in rules_of(e)
        and e.suggestion == API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    ]
    empty = [
        e
        for e in after_arm.values()
        if API_ZERO_HIT_FALLBACK_ANY_RULE in rules_of(e)
        and e.suggestion == API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
    ]
    assert [e.seq for e in served] == [S_SEQ] and [e.seq for e in empty] == [E_SEQ]
    assert sum(e.hit_count or 0 for e in served) == 2
    assert sum(e.rendered_rows or 0 for e in served) == 2


# ── The eval schema and the ask-your-docs reader are unchanged ────────────

# ``ToolEvent``'s fields as the eval schema declared them at 9b9de5a3.
TOOL_EVENT_FIELDS = (
    "event_id",
    "trajectory_id",
    "seq",
    "ts",
    "turn",
    "tool",
    "args",
    "latency_ms",
    "initiator",
    "error",
    "result_ids",
    "hit_count",
    "rendered_rows",
    "truncated",
    "suggestion",
    "fired_rules",
    "result_preview",
    "result_blob",
    "result_bytes",
)


def test_the_eval_schema_is_unchanged() -> None:
    assert SCHEMA_VERSION == 2
    assert tuple(f.name for f in dataclasses.fields(ToolEvent)) == TOOL_EVENT_FIELDS


async def test_the_ask_reader_folds_the_fallback_arm_to_the_same_events(tmp_path: Path) -> None:
    """The paid run's own reader (no turn sidecar here, so each call is its own
    turn) yields exactly the events of the shared merge."""
    trajectory_dir = await record_arm(tmp_path, fallback_on=True)
    loaded = load_ask_trajectory_events(trajectory_dir)
    assert loaded.turns_recorded is False
    assert {e.seq: e for e in loaded.events} == merge_server_events(trajectory_dir)
