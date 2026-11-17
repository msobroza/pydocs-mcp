"""The ``kind="api"`` zero-hit fallback as a trace reader sees it (ADR 0027, ADR 0009 / 0010).

Every call here is served through the production dispatch path: a real
``ToolRouter`` over the shared named router fakes, registered by the server's
own tool registration on a real ``TracingToolServer``, whose ``TraceRecorder``
writes ``server_events.jsonl``. The tests read that file back, the way the
eval merge and the paid before/after report do.

What an analyst must be able to tell apart on each call, kept as separate
assertions:

- the requested kind: ``args.kind == "api"``, the model's own request;
- the raw API zero hit: a ``suggestion_fired`` record with the fallback rule,
  keyed to the call's ``seq`` (primary, ADR 0010);
- the served rows: ``suggestion`` is the served or empty label (the echo), and
  ``hit_count`` / ``result_ids`` / ``rendered_rows`` describe the rows the
  response actually carried.

Every marker is a new VALUE in a field the recorder already writes, so the
event and record key sets and the trace schema version are pinned to their
pre-fallback literals. The new markers (the two label texts, the fallback
rule name and the failure prefix) are imported from the product, never
retyped; the pre-existing rule names and retry text a flag-off trace carries
are pinned as their 9b9de5a3 literals, the independent record of "today".
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest

from pydocs_mcp.application.api_zero_hit_fallback import API_ZERO_HIT_FALLBACK_FAILURE_PREFIX
from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import MultiProjectLookup, MultiProjectSearch
from pydocs_mcp.application.suggestions import (
    API_ZERO_HIT_FALLBACK_ANY_RULE,
    API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import Chunk
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.trace_writer import RESULT_BLOBS_DIRNAME, SERVER_EVENTS_FILENAME
from pydocs_mcp.observability.tracing_server import TracingToolServer
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.server import _register_tools

from tests.application._router_fakes import (
    RaisingDocsSearch,
    RecordingApiSearch,
    RecordingDocsSearch,
    make_envelope,
    make_service,
)

TOOL = "search_codebase"
QUERY = "nested serialization"
TRAJECTORY_ID = "traj-fallback"
API_REQUEST = {"query": QUERY, "kind": "api"}
DIRECT_ANY_REQUEST = {"query": QUERY, "kind": "any"}


def _chunk_row(chunk_id: int, title: str, start: int, end: int) -> Chunk:
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


# Two kind="any" rows — methods, which no member row holds, so the API pass
# misses them and only the fallback pass can serve them.
FALLBACK_ROWS = (_chunk_row(11, "Tree.walk", 3, 9), _chunk_row(12, "Tree.dump", 12, 20))


@dataclass
class TracedTrajectory:
    """One traced server process's ``server_events.jsonl``, read back."""

    trace_root: Path
    records: list[dict[str, Any]]
    results: list[types.CallToolResult]

    @property
    def header(self) -> dict[str, Any]:
        return self.records[0]

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [r for r in self.records if r["_event"] == "tool_call"]

    @property
    def fired_records(self) -> list[dict[str, Any]]:
        return [r for r in self.records if r["_event"] == "suggestion_fired"]

    def fired_rules_of(self, call: dict[str, Any]) -> list[str]:
        """The rules fired during ``call``, in emission order."""
        return [r["rule"] for r in self.fired_records if r["seq"] == call["seq"]]

    def blob_of(self, call: dict[str, Any]) -> dict[str, Any]:
        """The full serialized envelope the call's result blob holds."""
        blob = self.trace_root / RESULT_BLOBS_DIRNAME / call["result_blob"]
        return json.loads(blob.read_bytes())


# Every zero-hit flag set explicitly, so no test leans on a shipped default.
FALLBACK_ON = SuggestionsConfig(
    api_zero_hit_fallback_any=True, search_zero_hit=True, api_zero_hit_retry_any=True
)
FALLBACK_ON_ZERO_HIT_RULE_OFF = SuggestionsConfig(
    api_zero_hit_fallback_any=True, search_zero_hit=False, api_zero_hit_retry_any=True
)
FALLBACK_OFF = SuggestionsConfig(
    api_zero_hit_fallback_any=False, search_zero_hit=True, api_zero_hit_retry_any=True
)
# The two pre-existing zero-hit rules, as 9b9de5a3 names them.
TODAY_ZERO_HIT_RULE = "search_zero_hit"
TODAY_RETRY_RULE = "api_zero_hit_retry_any"


def _search_router(docs: Any, suggestions: SuggestionsConfig) -> ToolRouter:
    service = dataclasses.replace(make_service("solo"), docs=docs, api=RecordingApiSearch())
    services = (service,)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
        suggestions=suggestions,
    )


async def _dispatch(server: TracingToolServer, arguments: dict[str, Any]) -> types.CallToolResult:
    """Drive the registered tools/call handler, as a client request would."""
    handler = server._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=TOOL, arguments=arguments),
    )
    result = await handler(request)
    assert isinstance(result.root, types.CallToolResult)
    return result.root


async def trace_search_calls(
    trace_root: Path,
    docs: Any,
    suggestions: SuggestionsConfig,
    calls: Sequence[dict[str, Any]],
) -> TracedTrajectory:
    """Serve ``calls`` in order through one traced server; read its trace back."""
    recorder = TraceRecorder(trace_dir=trace_root, trajectory_id=TRAJECTORY_ID)
    recorder.open_trace()
    server = TracingToolServer("pydocs-mcp", recorder=recorder)
    _register_tools(server, _search_router(docs, suggestions))
    try:
        results = [await _dispatch(server, dict(arguments)) for arguments in calls]
    finally:
        recorder.close()
    return read_traced_trajectory(trace_root, results)


def read_traced_trajectory(
    trace_root: Path, results: list[types.CallToolResult]
) -> TracedTrajectory:
    path = trace_root / TRAJECTORY_ID / SERVER_EVENTS_FILENAME
    records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    return TracedTrajectory(trace_root, records, results)


# ── S: the fallback served rows ───────────────────────────────────────────

# The identifier atoms of FALLBACK_ROWS, in served order.
SERVED_ROW_ATOMS = [
    {
        "id": "11",
        "qualified_name": "pkg.mod.Tree.walk",
        "path": "pkg/mod.py",
        "start_line": 3,
        "end_line": 9,
    },
    {
        "id": "12",
        "qualified_name": "pkg.mod.Tree.dump",
        "path": "pkg/mod.py",
        "start_line": 12,
        "end_line": 20,
    },
]


async def test_served_fallback_fires_its_rule_keyed_to_the_calls_own_seq(tmp_path: Path) -> None:
    traced = await trace_search_calls(
        tmp_path,
        RecordingDocsSearch(FALLBACK_ROWS),
        FALLBACK_ON,
        [DIRECT_ANY_REQUEST, API_REQUEST],
    )
    direct_any, fallback = traced.tool_calls
    assert fallback["seq"] == 2
    # The requested kind stays the model's own request.
    assert fallback["args"] == {"query": QUERY, "kind": "api"}
    # The raw API zero hit: one fallback record, keyed to this call only.
    assert traced.fired_rules_of(fallback) == [API_ZERO_HIT_FALLBACK_ANY_RULE]
    assert traced.fired_rules_of(direct_any) == []
    assert [(r["seq"], r["tool"]) for r in traced.fired_records] == [(2, TOOL)]


async def test_served_fallback_counters_describe_the_rows_actually_served(tmp_path: Path) -> None:
    traced = await trace_search_calls(
        tmp_path,
        RecordingDocsSearch(FALLBACK_ROWS),
        FALLBACK_ON,
        [DIRECT_ANY_REQUEST, API_REQUEST],
    )
    direct_any, fallback = traced.tool_calls
    served_items = traced.results[1].structuredContent["items"]
    # The echo tells served from empty.
    assert fallback["suggestion"] == API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    assert fallback["error"] is None
    # The served rows: the fallback's own, in served order — what a direct
    # kind="any" request with the same arguments returns.
    assert fallback["hit_count"] == len(served_items) == 2
    assert fallback["result_ids"] == SERVED_ROW_ATOMS == direct_any["result_ids"]
    assert fallback["truncated"] is direct_any["truncated"] is False


async def test_served_fallback_rendered_rows_count_the_rows_of_the_served_text(
    tmp_path: Path,
) -> None:
    traced = await trace_search_calls(
        tmp_path, RecordingDocsSearch(FALLBACK_ROWS), FALLBACK_ON, [DIRECT_ANY_REQUEST, API_REQUEST]
    )
    direct_any, fallback = traced.tool_calls
    # Each rendered row opens with its own heading line, in served order.
    row_headings = [
        line for line in traced.blob_of(fallback)["text"].split("\n") if line.startswith("## ")
    ]
    assert row_headings == [
        "## pkg.mod.Tree.walk — pkg/mod.py:3-9",
        "## pkg.mod.Tree.dump — pkg/mod.py:12-20",
    ]
    assert fallback["rendered_rows"] == len(row_headings)
    assert fallback["rendered_rows"] == direct_any["rendered_rows"]


async def test_served_fallback_blob_holds_the_label_and_rows_of_their_own_kind(
    tmp_path: Path,
) -> None:
    traced = await trace_search_calls(
        tmp_path, RecordingDocsSearch(FALLBACK_ROWS), FALLBACK_ON, [API_REQUEST]
    )
    (fallback,) = traced.tool_calls
    blob = traced.blob_of(fallback)
    lines = blob["text"].split("\n")
    # The label is the first body line, right after the freshness header.
    assert lines[0].startswith("[index: ") and lines[1] == ""
    assert lines[2] == API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION
    assert "No symbols found." not in blob["text"]
    # Fallback rows are never presented as symbol hits: each keeps its own kind.
    assert [item["kind"] for item in blob["items"]] == ["chunk", "chunk"]
    assert blob["meta"]["suggestion"] == API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION


# ── E: both passes found nothing ──────────────────────────────────────────


async def test_empty_fallback_with_zero_hit_rule_on_logs_both_rules_in_order(
    tmp_path: Path,
) -> None:
    traced = await trace_search_calls(
        tmp_path / "on", RecordingDocsSearch(), FALLBACK_ON, [API_REQUEST]
    )
    today = await trace_search_calls(
        tmp_path / "off", RecordingDocsSearch(), FALLBACK_OFF, [API_REQUEST]
    )
    (empty,), (today_call,) = traced.tool_calls, today.tool_calls
    assert empty["args"] == {"query": QUERY, "kind": "api"}
    assert traced.fired_rules_of(empty) == [TODAY_ZERO_HIT_RULE, API_ZERO_HIT_FALLBACK_ANY_RULE]
    assert empty["suggestion"] == API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
    assert empty["hit_count"] == 0 and empty["result_ids"] == []
    # The empty response's rendered rows and truncation are the API pass's: today's.
    assert empty["rendered_rows"] == today_call["rendered_rows"] == 0
    assert empty["truncated"] is today_call["truncated"] is False


async def test_empty_fallback_with_zero_hit_rule_off_logs_the_fallback_rule_only(
    tmp_path: Path,
) -> None:
    traced = await trace_search_calls(
        tmp_path, RecordingDocsSearch(), FALLBACK_ON_ZERO_HIT_RULE_OFF, [API_REQUEST]
    )
    (empty,) = traced.tool_calls
    assert empty["args"] == {"query": QUERY, "kind": "api"}
    assert traced.fired_rules_of(empty) == [API_ZERO_HIT_FALLBACK_ANY_RULE]
    assert empty["suggestion"] == API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION
    assert empty["hit_count"] == 0 and empty["result_ids"] == []


# ── X: the fallback pass raised ───────────────────────────────────────────

EMBEDDER_DOWN = RuntimeError("embedder down")
# The fixed failure message (ADR 0027): the prefix, then the original
# exception's class name and text.
FAILED_FALLBACK_MESSAGE = f"{API_ZERO_HIT_FALLBACK_FAILURE_PREFIX}: RuntimeError: embedder down"


async def test_failed_fallback_handed_to_the_recorder_is_a_typed_error_with_no_fired_record(
    tmp_path: Path,
) -> None:
    """The recorder seam: the router's own exception, recorded as raised."""
    router = _search_router(RaisingDocsSearch(EMBEDDER_DOWN), FALLBACK_ON)
    recorder = TraceRecorder(trace_dir=tmp_path, trajectory_id=TRAJECTORY_ID)
    recorder.open_trace()
    try:
        seq = recorder.begin_tool_call()
        with pytest.raises(ServiceUnavailableError) as raised:
            await router.search_codebase(SearchInput(query=QUERY, kind="api"))
        await recorder.record_tool_failure(
            seq=seq, tool=TOOL, args=API_REQUEST, error=raised.value, latency_ms=1.0
        )
    finally:
        recorder.close()
    traced = read_traced_trajectory(tmp_path, [])
    (failed,) = traced.tool_calls
    assert failed["args"] == {"query": QUERY, "kind": "api"}
    assert failed["error"] == {
        "type": "ServiceUnavailableError",
        "message": FAILED_FALLBACK_MESSAGE,
        "cause": "RuntimeError",
    }
    assert failed["error"]["message"].startswith(API_ZERO_HIT_FALLBACK_FAILURE_PREFIX)
    assert traced.fired_records == []
    assert failed["suggestion"] is None and failed["hit_count"] is None


async def test_failed_fallback_on_the_traced_dispatch_path_keeps_the_typed_error_as_cause(
    tmp_path: Path,
) -> None:
    """The real traced server: FastMCP wraps every handler exception in its
    ``ToolError`` before the tracing subclass sees it (pre-existing for every
    tool, ADR 0009), so a paid trace records the wrapper, with the typed error
    as its cause and the fixed failure message inside its message. A trace
    reader therefore finds the prefix INSIDE the message, never at its start."""
    traced = await trace_search_calls(
        tmp_path, RaisingDocsSearch(EMBEDDER_DOWN), FALLBACK_ON, [API_REQUEST]
    )
    (failed,) = traced.tool_calls
    assert traced.results[0].isError is True
    assert failed["args"] == {"query": QUERY, "kind": "api"}
    assert failed["error"] == {
        "type": "ToolError",
        "message": f"Error executing tool {TOOL}: {FAILED_FALLBACK_MESSAGE}",
        "cause": "ServiceUnavailableError",
    }
    assert traced.fired_records == []
    assert failed["suggestion"] is None
    assert failed["hit_count"] is None and failed["result_ids"] is None
    assert failed["rendered_rows"] is None and failed["result_blob"] is None


# ── F off: today's trace, with none of the new markers ────────────────────

# The kind="api" retry line as 9b9de5a3 renders it for this request.
TODAY_RETRY_SUGGESTION = (
    '[suggestion: kind="api" matched nothing — retry with search_codebase('
    'query="nested serialization", kind="any") for a descriptive query, or give a name fragment]'
)
NEW_MARKERS = (
    API_ZERO_HIT_FALLBACK_ANY_RULE,
    API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION,
    API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    API_ZERO_HIT_FALLBACK_FAILURE_PREFIX,
)


def _strings_in(value: object) -> Iterator[str]:
    """Every string value nested in a decoded JSON document — markers are
    searched in decoded values, never in escaped JSON bytes."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings_in(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings_in(nested)


def new_markers_in(traced: TracedTrajectory) -> set[str]:
    """The fallback markers found anywhere in the trace records or result blobs."""
    blobs = [traced.blob_of(call) for call in traced.tool_calls if call["result_blob"]]
    strings = list(_strings_in(traced.records)) + list(_strings_in(blobs))
    return {marker for marker in NEW_MARKERS if any(marker in s for s in strings)}


@pytest.mark.parametrize(
    "docs_factory",
    [lambda: RecordingDocsSearch(FALLBACK_ROWS), lambda: RaisingDocsSearch(EMBEDDER_DOWN)],
    ids=["any-would-serve", "any-would-raise"],
)
async def test_fallback_off_traces_todays_zero_hit_with_none_of_the_new_markers(
    docs_factory: Callable[[], Any], tmp_path: Path
) -> None:
    docs = docs_factory()
    traced = await trace_search_calls(tmp_path, docs, FALLBACK_OFF, [API_REQUEST])
    (call,) = traced.tool_calls
    assert docs.queries == []  # no kind="any" pass ran
    assert call["args"] == {"query": QUERY, "kind": "api"}
    assert traced.fired_rules_of(call) == [TODAY_ZERO_HIT_RULE, TODAY_RETRY_RULE]
    assert call["suggestion"] == TODAY_RETRY_SUGGESTION
    assert call["error"] is None and call["truncated"] is False
    # A raw API zero hit on a pre-fallback trace is exactly ``hits == 0``.
    assert call["hit_count"] == 0 and call["result_ids"] == [] and call["rendered_rows"] == 0
    assert new_markers_in(traced) == set()


# ── Every outcome: new values in existing fields only ─────────────────────

OUTCOMES: dict[str, tuple[Callable[[], Any], SuggestionsConfig]] = {
    "served": (lambda: RecordingDocsSearch(FALLBACK_ROWS), FALLBACK_ON),
    "empty-zero-hit-rule-on": (lambda: RecordingDocsSearch(), FALLBACK_ON),
    "empty-zero-hit-rule-off": (lambda: RecordingDocsSearch(), FALLBACK_ON_ZERO_HIT_RULE_OFF),
    "failed": (lambda: RaisingDocsSearch(EMBEDDER_DOWN), FALLBACK_ON),
    "fallback-off": (lambda: RecordingDocsSearch(FALLBACK_ROWS), FALLBACK_OFF),
}
EXPECTED_MARKERS = {
    "served": {API_ZERO_HIT_FALLBACK_ANY_RULE, API_ZERO_HIT_FALLBACK_SERVED_SUGGESTION},
    "empty-zero-hit-rule-on": {
        API_ZERO_HIT_FALLBACK_ANY_RULE,
        API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    },
    "empty-zero-hit-rule-off": {
        API_ZERO_HIT_FALLBACK_ANY_RULE,
        API_ZERO_HIT_FALLBACK_EMPTY_SUGGESTION,
    },
    "failed": {API_ZERO_HIT_FALLBACK_FAILURE_PREFIX},
    "fallback-off": set(),
}
# The record shapes the recorder wrote at 9b9de5a3 (trace schema version 2).
TOOL_CALL_KEYS = (
    {"_event", "trajectory_id", "seq", "ts", "initiator", "tool", "args", "latency_ms"}
    | {"error", "result_ids", "hit_count", "rendered_rows", "truncated", "suggestion"}
    | {"result_preview", "result_blob", "result_bytes"}
)
FIRED_RECORD_KEYS = {"_event", "trajectory_id", "seq", "ts", "tool", "rule"}
HEADER_KEYS = {"_event", "trajectory_id", "schema_version", "artifact_hash", "ts"} | {
    "pydocs_mcp_version",
    "mcp_version",
}


async def _trace_outcome(outcome: str, trace_root: Path) -> TracedTrajectory:
    docs_factory, suggestions = OUTCOMES[outcome]
    return await trace_search_calls(trace_root, docs_factory(), suggestions, [API_REQUEST])


@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_every_outcome_keeps_the_trace_schema_and_record_shapes(
    outcome: str, tmp_path: Path
) -> None:
    traced = await _trace_outcome(outcome, tmp_path)
    assert set(traced.header) == HEADER_KEYS
    assert traced.header["schema_version"] == 2
    assert [set(call) for call in traced.tool_calls] == [TOOL_CALL_KEYS]
    assert all(set(record) == FIRED_RECORD_KEYS for record in traced.fired_records)


@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_every_outcome_carries_exactly_its_own_markers(outcome: str, tmp_path: Path) -> None:
    traced = await _trace_outcome(outcome, tmp_path)
    assert new_markers_in(traced) == EXPECTED_MARKERS[outcome]


@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_every_outcome_fires_a_rule_iff_it_echoes_a_suggestion(
    outcome: str, tmp_path: Path
) -> None:
    traced = await _trace_outcome(outcome, tmp_path)
    (call,) = traced.tool_calls
    assert bool(traced.fired_rules_of(call)) is bool(call["suggestion"])
