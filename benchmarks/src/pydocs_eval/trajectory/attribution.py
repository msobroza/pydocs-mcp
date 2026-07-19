"""Evidence attribution: surfaced → inspected → used tiers with first-touch
credit (ADR 0011 decision + action items 1, 5, 6; ADR 0010 schema semantics).

Consumes the canonical ``events.jsonl`` stream (``ToolEvent`` / ``LoopEvent``
records) plus the runner-captured final-patch file set, and assigns each
workspace file to the three cumulative tiers:

- **surfaced** — the file appears in any tool's ``result_ids`` (items-inclusive
  *enumeration* scope, ADR 0011: knowingly counts budget-elided search rows the
  token-budgeted text never rendered) or a loop-side Read.
- **inspected** — file *content* was returned by a content-classified tool/mode
  (the ``_CLASSIFICATION`` table below, keyed by tool+mode) or a loop Read.
- **used** — the file overlaps the final patch (file-level here; hunk-level
  overlap is computed in ``metrics`` from the span-bearing evidence recorded on
  each :class:`Surfacing`).
- **wasted-read** = inspected ∧ ¬used.

Two provenance rules (ADR 0011 R7): content whose first line exactly matches
``INJECTED_CONTEXT_MARKER`` is excluded from every tier (harness-injected, never
model-retrieved), and ``fired_rules`` machinery annotations NEVER add evidence
to any tier — this module never reads them for surfacing.

First-touch credit for a gold file goes to the single earliest surfacing event
in stream order (server ``seq`` orders tool events at merge time; loop Reads
keep their stream position). No credit splitting in Phase 2.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydocs_eval.trajectory.path_normalizer import normalize_path
from pydocs_eval.trajectory.schema import (
    LoopEvent,
    ToolEvent,
    parse_event_line,
)

# Contract copy (NOT imported — the eval package keeps a zero-``pydocs_mcp``
# floor, ADR 0009 placement). Mirrors
# ``pydocs_mcp.application.session_start_context.INJECTED_CONTEXT_MARKER``
# byte-for-byte; a drift is caught by the provenance-exclusion test.
INJECTED_CONTEXT_MARKER = (
    "[pydocs-mcp session-start-context: harness-injected at session start; not model-retrieved]"
)

# The loop's client-side Read tool: verbatim ``cat``-style file window → always
# content, always hunk-capable (exact live-disk lines).
_READ_TOOL = "Read"


class ContentClass(StrEnum):
    """Whether a tool/mode's rows put file *content* in front of the model.

    Only ``CONTENT`` qualifies a file for the **inspected** tier (ADR 0011's
    content-classified list). ``CONTENT_EXTRACT`` (api member rows: signature +
    docstring) and ``DERIVED`` (mined decision rationale) are surfaced-only —
    the ADR deliberately keeps them out of *inspected*. ``HIT_LIST`` is
    identifiers/paths only.
    """

    CONTENT = "content"
    CONTENT_EXTRACT = "content_extract"
    DERIVED = "derived"
    HIT_LIST = "hit_list"


class Fidelity(StrEnum):
    """Line fidelity of a tool/mode's spans for the **used** hunk-overlap check.

    ``HUNK`` rows carry real line spans (chunk/read/grep/get_symbol/get_context
    node spans); ``FILE`` rows have no reliable at-use-site lines (member rows,
    decision rows, get_references' defining-node spans, glob, overview). Hunk
    metrics are emitted EXCLUSIVELY from ``HUNK`` evidence (ADR 0011).
    """

    HUNK = "hunk"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class ToolModeClass:
    """One row of the per-tool/mode classification table (ADR 0011 action item 1).

    ``mode`` is the arg value selecting this row (``search_codebase.kind`` /
    ``get_symbol.depth`` / ``grep.output_mode``); ``None`` matches any mode of
    the tool. ``source`` cites the verified evidence (result-shapes §3/§4).
    """

    tool: str
    mode: str | None
    content_class: ContentClass
    fidelity: Fidelity
    source: str


# The classification table AS DATA (ADR 0011 action item 1), one source comment
# per row citing ``2026-07-18-phase2-evidence-result-shapes.md`` §3/§4. Order
# matters: the first row whose (tool, mode) matches wins; a trailing
# mode=None row is the tool's default.
_CLASSIFICATION: tuple[ToolModeClass, ...] = (
    # get_overview: ranked module qnames + one first-doc-line each — no file bytes.
    ToolModeClass(
        "get_overview", None, ContentClass.HIT_LIST, Fidelity.FILE, "result-shapes §3 overview card"
    ),
    # search_codebase chunk rows (kind=docs/any/default): FULL chunk text, real
    # v15 node spans → CONTENT, hunk-capable.
    ToolModeClass(
        "search_codebase",
        "docs",
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "result-shapes §3 _chunk_piece",
    ),
    ToolModeClass(
        "search_codebase",
        "any",
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "kind=any includes chunk rows",
    ),
    # kind=api member rows: signature + docstring, best-effort span → surfaced-
    # only (NOT inspected, ADR 0011), file-level fidelity.
    ToolModeClass(
        "search_codebase",
        "api",
        ContentClass.CONTENT_EXTRACT,
        Fidelity.FILE,
        "result-shapes §3 _member_piece",
    ),
    # kind=decision: mined rationale, null span by contract.
    ToolModeClass(
        "search_codebase",
        "decision",
        ContentClass.DERIVED,
        Fidelity.FILE,
        "result-shapes §3 decision rows",
    ),
    # default search (no kind arg) returns chunk rows → CONTENT.
    ToolModeClass(
        "search_codebase",
        None,
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "default kind returns chunk rows",
    ),
    # get_symbol depth=source: verbatim source fence → CONTENT, hunk-capable.
    ToolModeClass(
        "get_symbol",
        "source",
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "result-shapes §3 depth=source fence",
    ),
    # depth=summary/tree: PageIndex outline, NO text field → surfaced-only, but
    # rows carry node spans (hunk-capable if it later reaches the patch).
    ToolModeClass(
        "get_symbol",
        "summary",
        ContentClass.HIT_LIST,
        Fidelity.HUNK,
        "result-shapes §3 no text field",
    ),
    ToolModeClass(
        "get_symbol", "tree", ContentClass.HIT_LIST, Fidelity.HUNK, "result-shapes §3 no text field"
    ),
    ToolModeClass(
        "get_symbol", None, ContentClass.CONTENT, Fidelity.HUNK, "get_symbol default depth=source"
    ),
    # get_context: skeleton render with full bodies for central nodes → CONTENT.
    ToolModeClass(
        "get_context",
        None,
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "result-shapes §3 get_context card",
    ),
    # get_references: qname edge lists, NO file content; spans are the DEFINING
    # node, not call sites → hit list, file-level (ADR 0011).
    ToolModeClass(
        "get_references",
        None,
        ContentClass.HIT_LIST,
        Fidelity.FILE,
        "result-shapes §4 defining-node span",
    ),
    # get_why: mined decision rationale, locators are strings → derived content.
    ToolModeClass(
        "get_why", None, ContentClass.DERIVED, Fidelity.FILE, "result-shapes §3 get_why rows"
    ),
    # grep content mode: matched lines + context → CONTENT, exact live lines.
    ToolModeClass(
        "grep", "content", ContentClass.CONTENT, Fidelity.HUNK, "result-shapes §3 file:line:content"
    ),
    # grep files_with_matches / count: text body is paths-only; items LEAK one
    # matched line per file. Classified surfaced-not-inspected (ADR 0011 — one
    # leaked line is not a read); the items-leak bias is documented here.
    ToolModeClass(
        "grep",
        "files_with_matches",
        ContentClass.HIT_LIST,
        Fidelity.HUNK,
        "result-shapes §3 grep items-leak",
    ),
    ToolModeClass(
        "grep", "count", ContentClass.HIT_LIST, Fidelity.HUNK, "result-shapes §3 grep items-leak"
    ),
    ToolModeClass(
        "grep", None, ContentClass.CONTENT, Fidelity.HUNK, "grep default output_mode=content"
    ),
    # glob: path + mtime only → pure hit list, file-level.
    ToolModeClass(
        "glob", None, ContentClass.HIT_LIST, Fidelity.FILE, "result-shapes §3 glob paths only"
    ),
    # read_file: cat -n numbered verbatim window → CONTENT, exact live lines.
    ToolModeClass(
        "read_file", None, ContentClass.CONTENT, Fidelity.HUNK, "result-shapes §3 read_file window"
    ),
    # Loop-side client Read tool: verbatim file window (ADR 0011 inspected list).
    ToolModeClass(
        _READ_TOOL,
        None,
        ContentClass.CONTENT,
        Fidelity.HUNK,
        "ADR 0011 loop-side Read is inspected",
    ),
)

# Which arg selects a tool's mode. A tool absent here has no mode dimension.
_MODE_ARG: dict[str, str] = {
    "search_codebase": "kind",
    "get_symbol": "depth",
    "grep": "output_mode",
}

# Fallback for a tool the table does not know (unknown/future tool): conservative
# — surfaced-only, file-level, so an unclassified tool never fabricates an
# inspected/hunk credit.
_UNKNOWN_CLASS = ToolModeClass(
    "?", None, ContentClass.HIT_LIST, Fidelity.FILE, "unknown tool — conservative fallback"
)


def _mode_for(tool: str, args: dict[str, object]) -> str | None:
    """Return the mode string selecting a classification row, or ``None``."""
    arg = _MODE_ARG.get(tool)
    if arg is None:
        return None
    value = args.get(arg)
    return value if isinstance(value, str) else None


def classify(tool: str, args: dict[str, object]) -> ToolModeClass:
    """Classify a tool call by (tool, mode) against ``_CLASSIFICATION``.

    First an exact (tool, mode) match, then the tool's mode=None default row,
    then the conservative unknown-tool fallback.

    Example:
        >>> classify("read_file", {}).content_class is ContentClass.CONTENT
        True
        >>> classify("glob", {}).content_class is ContentClass.HIT_LIST
        True
    """
    mode = _mode_for(tool, args)
    for row in _CLASSIFICATION:
        if row.tool == tool and row.mode == mode:
            return row
    for row in _CLASSIFICATION:
        if row.tool == tool and row.mode is None:
            return row
    return _UNKNOWN_CLASS


@dataclass(frozen=True, slots=True)
class Surfacing:
    """One (event → gold-matchable file) surfacing edge in stream order.

    ``order`` is the event's stream position (first-touch key). ``inspected``
    is True iff the classification is ``CONTENT`` (or a loop Read). ``spans`` are
    the 1-indexed ``(start, end)`` line ranges the event exposed for this file,
    empty when the row is file-level fidelity — hunk overlap uses these.
    """

    path: str
    tool: str
    order: int
    content_class: ContentClass
    fidelity: Fidelity
    inspected: bool
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class Attribution:
    """The tier assignment for one trajectory (ADR 0011 output, action item 6).

    ``surfacings`` is the full ordered edge list (fidelity-stamped); the file
    sets are the gold-matchable projections used by ``metrics``. ``first_touch``
    maps every surfaced file to the tool that first surfaced it.
    """

    surfacings: tuple[Surfacing, ...]
    surfaced_files: frozenset[str]
    inspected_files: frozenset[str]
    used_files: frozenset[str]
    wasted_reads: frozenset[str]
    first_touch: dict[str, str]

    def spans_for(self, path: str) -> tuple[tuple[int, int], ...]:
        """All hunk-fidelity line spans this trajectory exposed for ``path``."""
        seen: list[tuple[int, int]] = []
        for edge in self.surfacings:
            if edge.path == path and edge.fidelity is Fidelity.HUNK:
                seen.extend(edge.spans)
        return tuple(seen)


def _first_line(text: str | None) -> str | None:
    """First line of a possibly-multiline content string (``None`` stays None)."""
    if text is None:
        return None
    return text.split("\n", 1)[0]


def _is_injected(event: ToolEvent | LoopEvent) -> bool:
    """True when an event is harness-injected and excluded from all tiers.

    Two independent R7 signals: an explicit ``initiator == "injected"`` tool
    event, or content whose FIRST LINE exactly matches ``INJECTED_CONTEXT_MARKER``
    (the marker's own contract — ``session_start_context.py``).
    """
    if isinstance(event, ToolEvent):
        if event.initiator == "injected":
            return True
        return _first_line(event.result_preview) == INJECTED_CONTEXT_MARKER
    return _first_line(event.text) == INJECTED_CONTEXT_MARKER


def _spans_from_ids(
    result_ids: tuple[dict[str, object], ...] | None,
) -> tuple[tuple[int, int], ...]:
    """Extract 1-indexed ``(start_line, end_line)`` spans present on items."""
    spans: list[tuple[int, int]] = []
    for item in result_ids or ():
        start, end = item.get("start_line"), item.get("end_line")
        if isinstance(start, int) and isinstance(end, int):
            spans.append((start, end))
    return tuple(spans)


def _paths_from_ids(result_ids: tuple[dict[str, object], ...] | None) -> Iterator[str]:
    """Yield the native ``path`` of each item that carries one (edges omit it)."""
    for item in result_ids or ():
        path = item.get("path")
        if isinstance(path, str) and path:
            yield path


def _read_path(event: LoopEvent) -> str | None:
    """The ``file_path`` of a loop-side Read tool use, else ``None``."""
    if event.tool != _READ_TOOL or event.tool_input is None:
        return None
    value = event.tool_input.get("file_path")
    return value if isinstance(value, str) and value else None


def _tool_surfacings(event: ToolEvent, order: int, workspace_root: str) -> Iterator[Surfacing]:
    """Yield one :class:`Surfacing` per gold-matchable item path of a tool call."""
    klass = classify(event.tool, event.args)
    spans = _spans_from_ids(event.result_ids)
    inspected = klass.content_class is ContentClass.CONTENT
    for raw in _paths_from_ids(event.result_ids):
        norm = normalize_path(raw, workspace_root=workspace_root)
        if not norm.gold_matchable:
            continue
        yield Surfacing(
            path=norm.value,
            tool=event.tool,
            order=order,
            content_class=klass.content_class,
            fidelity=klass.fidelity,
            inspected=inspected,
            spans=spans,
        )


def _read_surfacing(event: LoopEvent, order: int, workspace_root: str) -> Surfacing | None:
    """A loop Read → one CONTENT/hunk :class:`Surfacing` (span unknown → empty)."""
    raw = _read_path(event)
    if raw is None:
        return None
    norm = normalize_path(raw, workspace_root=workspace_root)
    if not norm.gold_matchable:
        return None
    return Surfacing(
        path=norm.value,
        tool=_READ_TOOL,
        order=order,
        content_class=ContentClass.CONTENT,
        fidelity=Fidelity.HUNK,
        inspected=True,
        spans=(),
    )


def _collect_surfacings(
    events: Iterable[ToolEvent | LoopEvent], workspace_root: str
) -> list[Surfacing]:
    """Walk the stream in order, skipping injected events, gathering surfacings."""
    out: list[Surfacing] = []
    for order, event in enumerate(events):
        if _is_injected(event):
            continue
        if isinstance(event, ToolEvent):
            out.extend(_tool_surfacings(event, order, workspace_root))
        else:
            read = _read_surfacing(event, order, workspace_root)
            if read is not None:
                out.append(read)
    return out


def _first_touch(surfacings: list[Surfacing]) -> dict[str, str]:
    """Map each file to the tool of its earliest surfacing (by stream order)."""
    credit: dict[str, str] = {}
    for edge in sorted(surfacings, key=lambda s: s.order):
        credit.setdefault(edge.path, edge.tool)
    return credit


def attribute_trajectory(
    events: Iterable[ToolEvent | LoopEvent],
    *,
    final_patch_files: frozenset[str],
    workspace_root: str,
) -> Attribution:
    """Assign surfaced/inspected/used tiers + first-touch credit (ADR 0011).

    ``final_patch_files`` are the runner-captured ``git diff`` file set in the
    workspace-relative POSIX normal form (see :mod:`path_normalizer`).

    Example:
        >>> ev = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0,
        ...     turn=1, tool="read_file", args={}, latency_ms=1.0,
        ...     result_ids=({"path": "a.py", "start_line": 1, "end_line": 3},))
        >>> a = attribute_trajectory([ev], final_patch_files=frozenset({"a.py"}),
        ...     workspace_root="/ws")
        >>> a.used_files == frozenset({"a.py"}) and "a.py" in a.inspected_files
        True
    """
    surfacings = _collect_surfacings(events, workspace_root)
    surfaced = frozenset(s.path for s in surfacings)
    inspected = frozenset(s.path for s in surfacings if s.inspected)
    used = surfaced & final_patch_files
    return Attribution(
        surfacings=tuple(surfacings),
        surfaced_files=surfaced,
        inspected_files=inspected,
        used_files=used,
        wasted_reads=inspected - final_patch_files,
        first_touch=_first_touch(surfacings),
    )


def load_events(events_jsonl: Path) -> tuple[ToolEvent | LoopEvent, ...]:
    """Parse a canonical ``events.jsonl`` into its ordered tool/loop events.

    The header line and any unknown (open-world skipped) records are dropped;
    only ``ToolEvent`` / ``LoopEvent`` records are returned, in file order.
    """
    events: list[ToolEvent | LoopEvent] = []
    for line in events_jsonl.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed = parse_event_line(json.loads(stripped))
        if isinstance(parsed, (ToolEvent, LoopEvent)):
            events.append(parsed)
    return tuple(events)
