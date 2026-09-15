"""Apply the active QuestionScope to every MCP tool call (UI spec §6.3–§6.5).

Defaults fill what the model left empty; pins overwrite and fan out over
(project, branch) cells with labeled, merged results. The interceptor runs
in a child task with a COPIED context (langchain-core copies the config
context per tool call), so it reads the scope from contextvars that
``ask()`` set and reports back through in-place mutation of a container
``ask()`` created — the ``_reinspect_state`` precedent in agent.py.

Strict passthrough when no question is active: the eval binding invokes
the graph directly and never calls ``ask()``, so every call it makes goes
through unchanged (R11).
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from mcp.types import CallToolResult, TextContent

from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_SERVER_VALUES,
    QuestionScope,
    ScopeCell,
    ScopeCode,
    ScopeKind,
    ScopeSlice,
    log_scope_event,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

# Which corpus filters each tool accepts (pydocs_mcp.server): ``project`` on all
# nine; ``package`` on the two below; ``scope`` on search_codebase (code
# filter) and, from multi-branch P2, the changed/diff slices on both of
# SLICE_TOOLS.
PACKAGE_TOOLS = frozenset({"search_codebase", "get_overview"})
SLICE_TOOLS = frozenset({"search_codebase", "grep"})


class ToolCallRequestLike(Protocol):
    """The adapter's ``MCPToolCallRequest`` shape (duck-typed for tests)."""

    name: str
    args: dict[str, Any]

    def override(self, **overrides: Any) -> ToolCallRequestLike: ...


ToolCallHandler = Callable[[ToolCallRequestLike], Awaitable[CallToolResult]]


@dataclass(frozen=True, slots=True)
class ScopeRuntime:
    """What the interceptor needs beyond the scope: the workspace's branch
    listing, the server's capabilities, and the fan-out cap."""

    listing: WorkspaceBranchListing
    capabilities: ScopeCapabilities
    max_cells: int


EMPTY_SCOPE_RUNTIME = ScopeRuntime(
    listing=EMPTY_BRANCH_LISTING,
    capabilities=NO_SCOPE_CAPABILITIES,
    max_cells=ScopeDefaultsConfig().max_cells,
)


class BranchOrigin(StrEnum):
    """Where a call's branch came from — observed at the interceptor, never
    inferred from ``meta`` (which has no such field)."""

    DEFAULT = "default"
    PINNED = "pinned"
    AGENT_CHOSEN = "agent_chosen"
    SERVER = "server"


@dataclass(frozen=True, slots=True)
class CellObservation:
    tool: str
    project: str  # what was SENT ("" = union)
    branch: str  # what was SENT ("" = nothing)
    branch_origin: BranchOrigin
    slice: ScopeSlice
    meta: Mapping[str, Any]
    replaced: bool = False  # a model-passed argument was replaced (§6.3 rule a / E2)
    is_error: bool = False


class ScopeObservations:
    """Per-question record of every tool call's cell + ``meta``.

    Mutable on purpose: the interceptor appends from a copied-context child
    task, and only in-place mutation of a container created in ``ask()``
    travels back to the page.
    """

    def __init__(self) -> None:
        self._records: list[CellObservation] = []

    def append(self, record: CellObservation) -> None:
        self._records.append(record)

    def records(self) -> tuple[CellObservation, ...]:
        return tuple(self._records)

    def by_cell(self) -> dict[tuple[str, str], tuple[CellObservation, ...]]:
        """Records grouped by the sent ``(project, branch)``, cells sorted."""
        grouped: dict[tuple[str, str], list[CellObservation]] = {}
        for record in self._records:
            grouped.setdefault((record.project, record.branch), []).append(record)
        return {cell: tuple(grouped[cell]) for cell in sorted(grouped)}

    def __len__(self) -> int:
        return len(self._records)


ACTIVE_QUESTION_SCOPE: contextvars.ContextVar[QuestionScope | None] = contextvars.ContextVar(
    "active_question_scope", default=None
)
ACTIVE_SCOPE_RUNTIME: contextvars.ContextVar[ScopeRuntime | None] = contextvars.ContextVar(
    "active_scope_runtime", default=None
)
ACTIVE_SCOPE_OBSERVATIONS: contextvars.ContextVar[ScopeObservations | None] = (
    contextvars.ContextVar("active_scope_observations", default=None)
)


# --- results -----------------------------------------------------------------


def cell_label(cell: ScopeCell, sent_args: Mapping[str, Any]) -> str:
    """Names what was SENT (UI spec §6.4 rule 3 under D14): the branch appears only when
    the per-cell arguments carried it — never on U0, even for a cell that holds one."""
    branch = str(sent_args.get("branch") or "")
    return f"{cell.project} · {branch}" if branch else cell.project


def _result_meta(result: CallToolResult) -> dict[str, Any]:
    structured = result.structuredContent or {}
    meta = structured.get("meta") or {}
    return dict(meta) if isinstance(meta, Mapping) else {}


def _result_text(result: CallToolResult) -> str:
    structured = result.structuredContent or {}
    if "text" in structured:
        return str(structured["text"])
    return "".join(getattr(block, "text", "") for block in result.content)


def _slice_of(args: Mapping[str, Any]) -> ScopeSlice:
    value = args.get("scope")
    if value == "changed":
        return ScopeSlice.CHANGED_FILES
    return ScopeSlice.DIFF_HUNKS if value == "diff" else ScopeSlice.WHOLE_BRANCH


def too_many_cells_result(count: int, cap: int) -> CallToolResult:
    """An ``isError`` result (never an exception): the adapter renders it as an
    error ToolMessage the model can read, whereas a bare exception escapes."""
    text = (
        f"scope pin spans {count} (project, branch) cells; the limit is max_cells={cap} "
        "(ask_your_docs.scope.max_cells). Narrow the pin or pass branch=<name>."
    )
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)


def merge_cell_results(
    cells: Sequence[ScopeCell],
    sent: Sequence[Mapping[str, Any]],
    results: Sequence[CallToolResult],
) -> CallToolResult:
    """One labeled result per cell; the ``{text, items, meta}`` envelope shape
    is kept, ``meta`` is exactly the first cell's, ``isError`` only when all erred.

    ``sent`` is the per-cell argument dict each call carried, in cell order: the label
    names what went out, never what the cell holds (AC-51).
    """
    content: list[Any] = []
    texts: list[str] = []
    items: list[dict[str, Any]] = []
    errors = 0
    for cell, args, result in zip(cells, sent, results, strict=True):
        label = cell_label(cell, args)
        content.append(TextContent(type="text", text=f"## {label}\n"))
        content.extend(result.content)
        errors += int(bool(result.isError))
        texts.append(f"## {label}\n{_result_text(result)}")
        structured = result.structuredContent or {}
        items.extend(
            {**item, "project": cell.project, "branch": cell.branch}
            for item in structured.get("items", ())
        )
    merged = {"text": "\n".join(texts), "items": items, "meta": _result_meta(results[0])}
    return CallToolResult(content=content, structuredContent=merged, isError=errors == len(results))


# --- observation -------------------------------------------------------------


def _observe(
    observations: ScopeObservations | None,
    *,
    tool: str,
    project: str,
    branch: str,
    origin: BranchOrigin,
    args: Mapping[str, Any],
    result: CallToolResult,
    replaced: bool = False,
) -> None:
    if observations is None:
        return
    observations.append(
        CellObservation(
            tool=tool,
            project=project,
            branch=branch,
            branch_origin=origin,
            slice=_slice_of(args),
            meta=_result_meta(result),
            replaced=replaced,
            is_error=bool(result.isError),
        )
    )


# --- DEFAULT rules -----------------------------------------------------------


def _default_project(
    tool: str, args: dict[str, Any], scope: QuestionScope, listing: WorkspaceBranchListing
) -> tuple[str, bool]:
    """(project sent, replaced?) — rule (a): keep a known name, replace an unknown one."""
    passed = str(args.get("project") or "")
    fallback = scope.default_project
    if not passed:
        if fallback:
            args["project"] = fallback
        return fallback, False
    if not listing.has_projects or listing.knows_project(passed):
        return passed, False
    args["project"] = fallback
    log_scope_event(
        "scope_default_replaced", tool=tool, argument="project", passed=passed, replacement=fallback
    )
    return fallback, True


def _default_branch(
    tool: str, args: dict[str, Any], scope: QuestionScope, project: str, runtime: ScopeRuntime
) -> tuple[str, BranchOrigin, bool]:
    """(branch sent, origin, replaced?). U0 body: the server does not advertise
    ``branch``, so nothing is ever sent and a stray model argument is dropped."""
    args.pop("branch", None)
    return "", BranchOrigin.SERVER, False


def _default_package(tool: str, args: dict[str, Any], scope: QuestionScope) -> None:
    if tool in PACKAGE_TOOLS and scope.package and not args.get("package"):
        args["package"] = scope.package


def _scope_argument_value(
    tool: str, scope: QuestionScope, capabilities: ScopeCapabilities
) -> str | None:
    """The ``scope`` value the scope implies for ``tool`` (U0: the code filter only)."""
    if tool == "search_codebase" and scope.code is not ScopeCode.ALL:
        return CODE_SERVER_VALUES[scope.code]
    return None


async def _apply_defaults(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    scope: QuestionScope,
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    args = dict(request.args)
    project, project_replaced = _default_project(request.name, args, scope, runtime.listing)
    branch, origin, branch_replaced = _default_branch(request.name, args, scope, project, runtime)
    _default_package(request.name, args, scope)
    value = _scope_argument_value(request.name, scope, runtime.capabilities)
    if value is not None and not args.get("scope"):
        args["scope"] = value  # DEFAULT injects only what the model omitted
    result = await handler(request.override(args=args))
    _observe(
        observations,
        tool=request.name,
        project=project,
        branch=branch,
        origin=origin,
        args=args,
        result=result,
        replaced=project_replaced or branch_replaced,
    )
    return result


# --- PIN rules ---------------------------------------------------------------


def cell_arguments(
    args: Mapping[str, Any], cell: ScopeCell, capabilities: ScopeCapabilities
) -> dict[str, Any]:
    """The per-cell arguments: the cell's project, plus its branch ONLY when
    the server advertises ``branch`` and the cell names one (AC-6b)."""
    out = dict(args)
    out["project"] = cell.project
    if capabilities.branch_selector and cell.branch:
        out["branch"] = cell.branch
    else:
        out.pop("branch", None)
    return out


def _cells_matching_branch(
    tool: str, cells: tuple[ScopeCell, ...], branch: str, project: str
) -> tuple[ScopeCell, ...]:
    """The pinned cells on ``branch`` (and ``project`` when named); empty + a log
    line when the model named a branch the pin does not hold."""
    matching = tuple(
        c for c in cells if c.branch == branch and (not project or c.project == project)
    )
    if not matching:
        log_scope_event(
            "scope_pin_branch_ignored",
            tool=tool,
            branch=branch,
            pinned=[f"{c.project}:{c.branch}" for c in cells],
        )
    return matching


def _cells_matching_project(tool: str, scope: QuestionScope, project: str) -> tuple[ScopeCell, ...]:
    """The pinned cells of ``project``; empty + a log line when it is not pinned."""
    by_project = tuple(c for c in scope.cells if c.project == project)
    if not by_project:
        log_scope_event(
            "scope_pin_project_ignored", tool=tool, project=project, pinned=scope.projects()
        )
    return by_project


def target_cells(
    tool: str, args: Mapping[str, Any], scope: QuestionScope, capabilities: ScopeCapabilities
) -> tuple[ScopeCell, ...]:
    """Which pinned cells a call covers (UI spec §6.4): a model-named pinned
    branch narrows to the matching cells; a pinned project narrows to its
    cells; anything else — the pin is hard — fans out over every cell."""
    passed_project = str(args.get("project") or "")
    passed_branch = str(args.get("branch") or "") if capabilities.branch_selector else ""
    if passed_branch:
        matching = _cells_matching_branch(tool, scope.cells, passed_branch, passed_project)
        if matching:
            return matching
    if passed_project:
        by_project = _cells_matching_project(tool, scope, passed_project)
        if by_project:
            return by_project
    return scope.cells


async def _call_pinned_cell(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    cell_args: Mapping[str, Any],
    cell: ScopeCell,
    observations: ScopeObservations | None,
) -> CallToolResult:
    """One handler call for one pinned cell, observed as PINNED. ``cell_args`` is built
    by ``cell_arguments`` once, so the caller can label the result from the same dict."""
    result = await handler(request.override(args=cell_args))
    _observe(
        observations,
        tool=request.name,
        project=cell.project,
        branch=cell_args.get("branch", ""),
        origin=BranchOrigin.PINNED,
        args=cell_args,
        result=result,
    )
    return result


async def fan_out_over_cells(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    args: Mapping[str, Any],
    cells: Sequence[ScopeCell],
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    """One handler call per cell, in cell order, merged with labels; the cap
    is checked BEFORE any call (E4)."""
    if len(cells) > runtime.max_cells:
        return too_many_cells_result(len(cells), runtime.max_cells)
    sent = [cell_arguments(args, cell, runtime.capabilities) for cell in cells]
    results = [
        await _call_pinned_cell(request, handler, cell_args, cell, observations)
        for cell, cell_args in zip(cells, sent, strict=True)
    ]
    return merge_cell_results(cells, sent, results)


async def _apply_pin(
    request: ToolCallRequestLike,
    handler: ToolCallHandler,
    scope: QuestionScope,
    runtime: ScopeRuntime,
    observations: ScopeObservations | None,
) -> CallToolResult:
    args = dict(request.args)
    if request.name in PACKAGE_TOOLS and scope.package:
        args["package"] = scope.package
    value = _scope_argument_value(request.name, scope, runtime.capabilities)
    if value is not None:
        args["scope"] = value  # a pin overwrites
    cells = target_cells(request.name, args, scope, runtime.capabilities)
    if len(cells) > 1:
        return await fan_out_over_cells(request, handler, args, cells, runtime, observations)
    cell_args = cell_arguments(args, cells[0], runtime.capabilities)
    return await _call_pinned_cell(request, handler, cell_args, cells[0], observations)


# --- entry point -------------------------------------------------------------


async def intercept_question_scope(
    request: ToolCallRequestLike, handler: ToolCallHandler
) -> CallToolResult:
    """The interceptor ``agent._intercept`` delegates to."""
    scope = ACTIVE_QUESTION_SCOPE.get()
    if scope is None:
        return await handler(request)  # no question active: strict passthrough
    runtime = ACTIVE_SCOPE_RUNTIME.get() or EMPTY_SCOPE_RUNTIME
    observations = ACTIVE_SCOPE_OBSERVATIONS.get()
    if scope.kind is ScopeKind.PIN:
        return await _apply_pin(request, handler, scope, runtime, observations)
    return await _apply_defaults(request, handler, scope, runtime, observations)


__all__ = (
    "ACTIVE_QUESTION_SCOPE",
    "ACTIVE_SCOPE_OBSERVATIONS",
    "ACTIVE_SCOPE_RUNTIME",
    "EMPTY_SCOPE_RUNTIME",
    "PACKAGE_TOOLS",
    "SLICE_TOOLS",
    "BranchOrigin",
    "CellObservation",
    "ScopeObservations",
    "ScopeRuntime",
    "cell_arguments",
    "cell_label",
    "fan_out_over_cells",
    "intercept_question_scope",
    "merge_cell_results",
    "target_cells",
    "too_many_cells_result",
)
