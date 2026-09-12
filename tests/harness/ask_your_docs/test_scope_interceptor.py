"""The scope interceptor — AC-1, 2, 3, 4, 6b, 8, 9, 10 (U0 rules)."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
from dataclasses import dataclass, replace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    ACTIVE_QUESTION_SCOPE,
    ACTIVE_SCOPE_OBSERVATIONS,
    ACTIVE_SCOPE_RUNTIME,
    BranchOrigin,
    ScopeObservations,
    ScopeRuntime,
    intercept_question_scope,
)
from pydocs_mcp.models import BranchStatus

NINE = (
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
BRANCHED = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)


def _row(name, *, default=False, base=None):
    return IndexedBranch(name, "a" * 40, base, default, BranchStatus.ACTIVE, None, None, 1.0)


LISTING = WorkspaceBranchListing(
    projects={
        "backend": (_row("feature/x", default=True, base="main"), _row("main")),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789"}),
)


@dataclass(frozen=True)
class FakeRequest:
    """The adapter's MCPToolCallRequest shape: name, args, override(args=)."""

    name: str
    args: dict[str, Any]
    server_name: str = "pydocs"

    def override(self, **overrides: Any) -> FakeRequest:
        return replace(self, **overrides)


def _result(
    text: str = "ok", *, meta: dict | None = None, items=(), error: bool = False
) -> CallToolResult:
    if error:
        return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)
    structured = {
        "text": text,
        "items": list(items),
        "meta": {"tool": "t", "project": "backend", **(meta or {})},
    }
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structuredContent=structured
    )


class RecordingHandler:
    """Records every request it receives; answers from a queue (or a default)."""

    def __init__(self, results: list[CallToolResult] | None = None) -> None:
        self.requests: list[FakeRequest] = []
        self.results = list(results or [])

    async def __call__(self, request: FakeRequest) -> CallToolResult:
        self.requests.append(request)
        return self.results.pop(0) if self.results else _result(f"answer for {request.args}")

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [r.args for r in self.requests]


@contextlib.contextmanager
def active(scope, runtime=None, observations=None):
    tokens = (
        ACTIVE_QUESTION_SCOPE.set(scope),
        ACTIVE_SCOPE_RUNTIME.set(runtime),
        ACTIVE_SCOPE_OBSERVATIONS.set(observations),
    )
    try:
        yield
    finally:
        ACTIVE_QUESTION_SCOPE.reset(tokens[0])
        ACTIVE_SCOPE_RUNTIME.reset(tokens[1])
        ACTIVE_SCOPE_OBSERVATIONS.reset(tokens[2])


def call(tool: str, args: dict, handler: RecordingHandler) -> CallToolResult:
    return asyncio.run(intercept_question_scope(FakeRequest(tool, dict(args)), handler))


DEFAULT_UNION = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),))
RUNTIME_U0 = ScopeRuntime(listing=LISTING, capabilities=NO_SCOPE_CAPABILITIES, max_cells=4)


@pytest.mark.parametrize("tool", NINE)
def test_ac1_no_active_question_is_a_strict_passthrough(tool):
    handler = RecordingHandler()
    args = {"project": "unknown-project", "scope": "deps", "package": "x", "branch": "b"}
    call(tool, args, handler)
    assert handler.sent == [args]


@pytest.mark.parametrize("tool", NINE)
@pytest.mark.parametrize("project", ["", "backend", "backend_0123456789"])
def test_ac2_shipped_defaults_send_the_models_arguments(tool, project):
    handler = RecordingHandler()
    args = {"query": "q", "project": project} if project else {"query": "q"}
    with active(DEFAULT_UNION, RUNTIME_U0):
        call(tool, args, handler)
    assert handler.sent == [args]


def test_ac3_unknown_project_is_replaced_and_logged(caplog):
    handler = RecordingHandler()
    with active(DEFAULT_UNION, RUNTIME_U0), caplog.at_level("INFO"):
        call("search_codebase", {"query": "q", "project": "frontend"}, handler)
    assert handler.sent == [{"query": "q", "project": ""}]
    record = json.loads(caplog.records[-1].getMessage())
    assert record == {
        "argument": "project",
        "event": "scope_default_replaced",
        "passed": "frontend",
        "replacement": "",
        "tool": "search_codebase",
    }


def test_ac3_named_default_replaces_with_that_project():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("tooling", ""),))
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("get_symbol", {"target": "a.b", "project": "frontend"}, handler)
        call("get_symbol", {"target": "a.b"}, handler)
    assert handler.sent == [{"target": "a.b", "project": "tooling"}] * 2


def test_ac3_empty_listing_never_replaces():
    handler = RecordingHandler()
    runtime = ScopeRuntime(
        listing=WorkspaceBranchListing({}), capabilities=NO_SCOPE_CAPABILITIES, max_cells=4
    )
    with active(DEFAULT_UNION, runtime):
        call("grep", {"pattern": "x", "project": "whatever"}, handler)
    assert handler.sent == [{"pattern": "x", "project": "whatever"}]


def test_ac4_code_default_touches_search_codebase_only():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), code=ScopeCode.OWN)
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("search_codebase", {"query": "q"}, handler)
        call("grep", {"pattern": "p"}, handler)
        call("search_codebase", {"query": "q", "scope": "deps"}, handler)
    assert handler.sent == [
        {"query": "q", "scope": "project"},
        {"pattern": "p"},
        {"query": "q", "scope": "deps"},
    ]


def test_default_package_is_injected_only_when_omitted():
    scope = QuestionScope(kind=ScopeKind.DEFAULT, cells=(ScopeCell("", ""),), package="fastapi")
    handler = RecordingHandler()
    with active(scope, RUNTIME_U0):
        call("get_overview", {}, handler)
        call("get_overview", {"package": "pydantic"}, handler)
        call("get_symbol", {"target": "a"}, handler)
    assert handler.sent == [{"package": "fastapi"}, {"package": "pydantic"}, {"target": "a"}]


def test_pin_overwrites_the_models_code_filter_and_package():
    """Where DEFAULT fills only what the model omitted, a PIN is hard (UI spec §6.3)."""
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("backend", ""),),
        code=ScopeCode.DEPS,
        package="fastapi",
    )
    handler = RecordingHandler()
    with active(pin, RUNTIME_U0):
        call("search_codebase", {"query": "q", "scope": "project", "package": "pydantic"}, handler)
        call("grep", {"pattern": "p", "package": "pydantic"}, handler)
    assert handler.sent == [
        {"query": "q", "scope": "deps", "package": "fastapi", "project": "backend"},
        {"pattern": "p", "package": "pydantic", "project": "backend"},
    ]


def test_ac6b_two_project_pin_fans_out_over_project_only():
    pin = QuestionScope(
        kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("tooling", "main"))
    )
    handler = RecordingHandler([_result("A"), _result("B")])
    with active(pin, RUNTIME_U0):
        merged = call("search_codebase", {"query": "q", "branch": "main"}, handler)
    assert handler.sent == [
        {"query": "q", "project": "backend"},
        {"query": "q", "project": "tooling"},
    ]
    # The label is the CELL (spec §6.4 rule 3); only the sent arguments drop the branch.
    texts = [b.text for b in merged.content]
    assert texts == ["## backend · main\n", "A", "## tooling · main\n", "B"]
    assert merged.structuredContent["text"] == "## backend · main\nA\n## tooling · main\nB"
    assert merged.isError is False


def test_pin_narrows_to_the_model_named_project_and_logs_an_unknown_one(caplog):
    pin = QuestionScope(
        kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"), ScopeCell("tooling", "main"))
    )
    handler = RecordingHandler()
    with active(pin, RUNTIME_U0), caplog.at_level("INFO"):
        call("get_overview", {"project": "tooling"}, handler)
        call("get_overview", {"project": "frontend"}, handler)
    assert handler.sent == [{"project": "tooling"}, {"project": "backend"}, {"project": "tooling"}]
    record = json.loads(caplog.records[-1].getMessage())
    assert record == {
        "event": "scope_pin_project_ignored",
        "pinned": ["backend", "tooling"],
        "project": "frontend",
        "tool": "get_overview",
    }


def test_ac8_fan_out_over_the_cap_is_refused_before_any_call():
    pin = QuestionScope(
        kind=ScopeKind.PIN,
        cells=(ScopeCell("a", "m"), ScopeCell("b", "m"), ScopeCell("c", "m")),
    )
    handler = RecordingHandler()
    runtime = ScopeRuntime(listing=LISTING, capabilities=NO_SCOPE_CAPABILITIES, max_cells=2)
    with active(pin, runtime):
        result = call("get_overview", {}, handler)
    assert handler.sent == []
    assert result.isError is True
    text = result.content[0].text
    assert "max_cells=2" in text and "ask_your_docs.scope.max_cells" in text


def test_ac9_partial_failure_keeps_the_error_text_under_its_label():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "m"), ScopeCell("b", "m")))
    handler = RecordingHandler([_result("fine"), _result("boom", error=True)])
    with active(pin, RUNTIME_U0):
        merged = call("get_overview", {}, handler)
    assert merged.isError is False
    assert [b.text for b in merged.content] == ["## a · m\n", "fine", "## b · m\n", "boom"]
    assert merged.structuredContent["text"] == "## a · m\nfine\n## b · m\nboom"
    handler = RecordingHandler([_result("x", error=True), _result("y", error=True)])
    with active(pin, RUNTIME_U0):
        merged = call("get_overview", {}, handler)
    assert merged.isError is True


def test_merged_items_carry_project_and_branch_and_meta_is_the_first_cells():
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("a", "m"), ScopeCell("b", "n")))
    handler = RecordingHandler(
        [
            _result("A", items=[{"id": 1}], meta={"branch": "m"}),
            _result("B", items=[{"id": 2}], meta={"branch": "n"}),
        ]
    )
    with active(pin, RUNTIME_U0):
        merged = call("search_codebase", {"query": "q"}, handler)
    assert merged.structuredContent["items"] == [
        {"id": 1, "project": "a", "branch": "m"},
        {"id": 2, "project": "b", "branch": "n"},
    ]
    assert merged.structuredContent["meta"] == {"tool": "t", "project": "backend", "branch": "m"}


def test_ac10_observations_record_origin_per_call():
    observations = ScopeObservations()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("backend", "main"),))
    handler = RecordingHandler([_result("A", meta={"branch": "main", "index_stale": True})])
    with active(pin, RUNTIME_U0, observations):
        call("get_symbol", {"target": "x"}, handler)
    (record,) = observations.records()
    assert (record.tool, record.project, record.branch_origin) == (
        "get_symbol",
        "backend",
        BranchOrigin.PINNED,
    )
    assert record.meta["index_stale"] is True and record.branch == ""  # nothing sent on U0
    observations = ScopeObservations()
    with active(DEFAULT_UNION, RUNTIME_U0, observations):
        call("get_symbol", {"target": "x"}, handler)
    assert observations.records()[0].branch_origin is BranchOrigin.SERVER


def test_ac10_copied_context_child_task_populates_the_same_container():
    """The tool node runs interceptors in child tasks with copied contexts."""
    observations = ScopeObservations()
    handler = RecordingHandler()

    async def _run() -> None:
        with active(DEFAULT_UNION, RUNTIME_U0, observations):
            task = asyncio.create_task(
                intercept_question_scope(FakeRequest("grep", {"pattern": "p"}), handler),
                context=contextvars.copy_context(),
            )
            await task

    asyncio.run(_run())
    assert len(observations) == 1


def test_by_cell_groups_sorted():
    observations = ScopeObservations()
    pin = QuestionScope(kind=ScopeKind.PIN, cells=(ScopeCell("b", ""), ScopeCell("a", "")))
    with active(pin, RUNTIME_U0, observations):
        call("get_overview", {}, RecordingHandler())
    assert list(observations.by_cell()) == [("a", ""), ("b", "")]
