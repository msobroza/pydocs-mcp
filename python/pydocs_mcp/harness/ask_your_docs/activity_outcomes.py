"""What a tool result says, in words: outcome, notes, citations (activity panel, PROPOSAL §3).

Reads the frozen MCP envelope ``{text, items, meta}`` (docs/tool-contracts.md §2–§3); a
result without one (an older server, the agent-local ``reinspect_images``) falls back to
the first line of its text. Notes are words, never colour alone. The ``text`` handed in is
already redacted by the trace builder; nothing here renders.

Example:
    >>> meta_notes({"truncated": True})
    ('Results were cut off at the limit',)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydocs_mcp.harness.ask_your_docs.activity_labels import (
    NOTE_MAX_CHARS,
    clip_label_text,
    lenient_int,
)

_REFERENCE_NOUNS = {
    "callers": ("caller", "callers"),
    "callees": ("callee", "callees"),
    "governed_by": ("decision", "decisions"),
}  # inherits / impact: edges
_FLAG_NOTES = (
    ("truncated", "Results were cut off at the limit"),
    ("index_stale", "The index is older than your checkout"),
)
_RESOLUTION_NOTES = {
    "syntactic": "Reference graph matches by name (syntactic), so some calls may be missed",
    "unavailable": "Reference graph not available for this language",
}
_NAME_FIELDS = ("qualified_name", "from_qualified_name", "to_qualified_name")

_Items = Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class Citation:
    """A file span a tool result pointed at — a chip under the step, a row in Sources."""

    path: str
    start_line: int | None
    end_line: int | None
    qualified_name: str | None
    package: str | None

    @property
    def label(self) -> str:
        """``path:line`` (or the bare path) — the chip's text."""
        return f"{self.path}:{self.start_line}" if self.start_line else self.path

    def redacted(self, redact: Callable[[str], str]) -> Citation:
        """This citation with ``redact`` applied to every text field (rows are tool output)."""
        name, package = self.qualified_name, self.package
        return replace(
            self,
            path=redact(self.path),
            qualified_name=redact(name) if name else None,
            package=redact(package) if package else None,
        )


def envelope_items(structured: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """The envelope's ``items`` rows (dicts only); ``[]`` without an envelope."""
    items = structured.get("items") if isinstance(structured, Mapping) else None
    return [row for row in items if isinstance(row, Mapping)] if isinstance(items, list) else []


def envelope_meta(structured: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The envelope's ``meta`` mapping; ``{}`` without one."""
    meta = structured.get("meta") if isinstance(structured, Mapping) else None
    return meta if isinstance(meta, Mapping) else {}


# ── outcomes ──


def summarize_tool_result(
    name: str, args: Mapping[str, Any], structured: Mapping[str, Any] | None, text: str
) -> str:
    """The short outcome after a successful call, from ``items`` / ``meta`` when present."""
    if name == "reinspect_images":
        return _reinspect_outcome(text)
    summarize = _OUTCOMES.get(name)
    if structured is None or summarize is None:
        return first_line(text)
    return summarize(args, envelope_items(structured), envelope_meta(structured))


def failure_outcome(text: str) -> str:
    """``failed: <first line>`` for a tool that reported an error."""
    first = first_line(text)
    return f"failed: {first}" if first else "failed"


def first_line(text: str) -> str:
    """The first non-blank line of ``text``, clipped for a one-line display."""
    lines = text.strip().splitlines()
    return clip_label_text(lines[0], NOTE_MAX_CHARS) if lines else ""


def _count(n: int, singular: str, plural: str) -> str:
    return f"no {plural}" if n == 0 else f"{n} {singular if n == 1 else plural}"


def _in_files(noun: tuple[str, str], items: _Items) -> str:
    files = len({row.get("path") for row in items if row.get("path")})
    return (
        f"{_count(len(items), *noun)} in {_count(files, 'file', 'files')}"
        if items
        else _count(0, *noun)
    )


def _span(row: Mapping[str, Any]) -> str:
    path = row.get("path")
    start, end = lenient_int(row.get("start_line")), lenient_int(row.get("end_line"))
    if not path:
        return ""
    if start and end and end != start:
        return f"{path}:{start}–{end}"
    return f"{path}:{start}" if start else str(path)


def _reinspect_outcome(text: str) -> str:
    # Function-local: the prompts package renders its templates on import.
    from pydocs_mcp.harness.ask_your_docs.prompts import BUDGET_MESSAGE

    return "budget used up" if text.strip() == BUDGET_MESSAGE.strip() else "done"


def _references_outcome(args: Mapping[str, Any], items: _Items, _meta: Mapping) -> str:
    noun = _REFERENCE_NOUNS.get(str(args.get("direction") or "callers"), ("edge", "edges"))
    return _count(len(items), *noun)


def _overview_outcome(_args: Mapping[str, Any], _items: _Items, meta: Mapping) -> str:
    return " · ".join(str(meta[key]) for key in ("project", "branch") if meta.get(key))


def _read_file_outcome(_args: Mapping[str, Any], items: _Items, _meta: Mapping) -> str:
    start = lenient_int(items[0].get("start_line")) if items else None
    end = lenient_int(items[0].get("end_line")) if items else None
    return _count(end - start + 1 if start and end else 0, "line", "lines")


_Outcome = Callable[[Mapping[str, Any], _Items, Mapping[str, Any]], str]
_OUTCOMES: dict[str, _Outcome] = {
    "search_codebase": lambda _a, items, _m: _in_files(("match", "matches"), items),
    "get_symbol": lambda _a, items, _m: _span(items[0]) if items else "no match",
    "get_context": lambda _a, items, _m: _count(len(items), "symbol", "symbols"),
    "get_references": _references_outcome,
    "get_why": lambda _a, items, _m: _count(len(items), "decision", "decisions"),
    "get_overview": _overview_outcome,
    "grep": lambda _a, items, _m: _in_files(("matching line", "matching lines"), items),
    "glob": lambda _a, items, _m: _count(len(items), "file", "files"),
    "read_file": _read_file_outcome,
}


# ── notes, the meta line, citations ──


def meta_notes(meta: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The envelope's flags as sentences: truncation, a stale index, a hint, resolution."""
    if not isinstance(meta, Mapping):
        return ()
    notes = [note for key, note in _FLAG_NOTES if meta.get(key) is True]
    suggestion = meta.get("suggestion")
    if isinstance(suggestion, str) and suggestion.strip():
        hint = suggestion.strip().removeprefix("[suggestion:").removesuffix("]").strip()
        notes.append(f"Tool hint: {clip_label_text(hint, NOTE_MAX_CHARS)}")
    resolution = _RESOLUTION_NOTES.get(str(meta.get("resolution")))
    return (*notes, resolution) if resolution else tuple(notes)


def meta_line(meta: Mapping[str, Any]) -> str:
    """The technical-details summary of ``meta``: project · branch · index · truncated."""
    parts = [f"{key} {meta[key]}" for key in ("project", "branch") if meta.get(key)]
    stale = meta.get("index_stale")
    if isinstance(stale, bool):
        parts.append("index older than checkout" if stale else "index up to date")
    if isinstance(meta.get("truncated"), bool):
        parts.append(f"truncated {'yes' if meta['truncated'] else 'no'}")
    parts += [f"resolution {meta['resolution']}"] if meta.get("resolution") else []
    return " · ".join(parts)


def citations_from_items(items: Iterable[Any] | None) -> tuple[Citation, ...]:
    """Rows that carry a ``path``, first occurrence per ``(path, start_line)``."""
    return unique_citations(filter(None, map(_citation, items or ())))


def unique_citations(citations: Iterable[Citation]) -> tuple[Citation, ...]:
    """``citations`` without repeats, first occurrence per ``(path, start_line)`` kept."""
    unique: dict[tuple[str, int | None], Citation] = {}
    for citation in citations:
        unique.setdefault((citation.path, citation.start_line), citation)
    return tuple(unique.values())


def _citation(row: Any) -> Citation | None:
    path = row.get("path") if isinstance(row, Mapping) else None
    if not isinstance(path, str) or not path:
        return None
    names = (row.get(field) for field in _NAME_FIELDS)
    name = next((value for value in names if isinstance(value, str) and value), None)
    package = row.get("package")
    return Citation(
        path=path,
        start_line=lenient_int(row.get("start_line")),
        end_line=lenient_int(row.get("end_line")),
        qualified_name=name,
        package=package if isinstance(package, str) else None,
    )


def split_cited(
    citations: Sequence[Citation], answer: str
) -> tuple[tuple[Citation, ...], tuple[Citation, ...]]:
    """``(cited, also looked at)``: cited = the answer names the path or the qualified name.

    The answer's markdown is never rewritten; this only sorts the Sources row.
    """
    cited = tuple(c for c in citations if _is_cited(c, answer))
    return cited, tuple(c for c in citations if c not in cited)


def _is_cited(citation: Citation, answer: str) -> bool:
    name = citation.qualified_name
    return citation.path in answer or bool(name and name in answer)


__all__ = (
    "Citation",
    "citations_from_items",
    "envelope_items",
    "envelope_meta",
    "failure_outcome",
    "first_line",
    "meta_line",
    "meta_notes",
    "split_cited",
    "summarize_tool_result",
    "unique_citations",
)
