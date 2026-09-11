"""Verbatim source for one indexed symbol — get_symbol(depth="source") (spec §D1/§D7).

The §D7 recovery chain terminates here: a truncated card points at
get_symbol(..., depth="source"), and if even one symbol exceeds the line cap
the rendered file path is the final, always-valid recovery step (readable by
the agent's own file tools).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.symbol_source_span import (
    SPAN_SOURCE_KINDS,
    indexed_lines_by_number,
    render_span,
    span_runs,
    window_end,
)
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

log = logging.getLogger(__name__)

_DEFAULT_MAX_LINES = 400


def _span_item(
    target: str, path: str, metadata: Mapping[str, Any], *, kind: str = ""
) -> dict[str, Any]:
    """The single §3.3 row for the rendered span (contract, Task 6).

    Chunk rows persist qualified_name + the v15 span but no node kind — the
    caller resolves ``kind`` from the document tree (the same source the
    summary/tree depths render from) so it stays identical across depths;
    "" when the tree/node is absent. Legacy (pre-v15) rows likewise degrade
    path/span to null instead of failing.
    """
    start = metadata.get("start_line")
    end = metadata.get("end_line")
    qname = str(metadata.get("qualified_name") or target)
    return {
        "node_id": qname,
        "kind": str(metadata.get("kind") or kind or ""),
        "qualified_name": qname,
        "path": path or None,
        "start_line": start if isinstance(start, int) else None,
        "end_line": end if isinstance(end, int) else None,
    }


def _cap_footer(elided: int, path: str, max_lines: int) -> str:
    """The §D7 terminal recovery line, and the ledger entry that earns it."""
    ledger = get_active_ledger()
    if ledger is not None:
        ledger.record(
            TruncationEntry(
                description=f"{elided} source lines beyond the {max_lines}-line cap",
                recovery="",  # the inline file path IS the terminal recovery
            )
        )
    return f"[… {elided} more lines — read {path or 'the source file'} directly]\n"


def _source_header(target: str, path: str) -> str:
    """The one card title both source renderings share."""
    return f"# Source — `{target}`" + (f"  ·  {path}" if path else "")


def _render_chunk_source(target: str, path: str, text: str, max_lines: int) -> str:
    """Render a chunk that already IS its span — a def slice, a heading body."""
    lines = text.splitlines()
    body = "\n".join(lines[:max_lines])
    out = f"{_source_header(target, path)}\n\n```python\n{body}\n```\n"
    if len(lines) <= max_lines:
        return out
    return out + _cap_footer(len(lines) - max_lines, path, max_lines)


def _render_span_source(node: DocumentNode, target: str, path: str, max_lines: int) -> str:
    """Rebuild a CLASS or MODULE span: verbatim fences plus gap markers (spec §2)."""
    indexed = indexed_lines_by_number(node)
    last = window_end(node.start_line, node.end_line, max_lines)
    body, gaps = render_span(span_runs(indexed, node.start_line, last), indexed, path)
    _log_span(node, last, gaps)
    out = f"{_source_header(target, path)}\n\n{body}"
    if node.end_line <= last:
        return out
    return out + _cap_footer(node.end_line - last, path, max_lines)


def _log_span(node: DocumentNode, last: int, gaps: int) -> None:
    """One debug line per rebuilt span — the window and how much of it is missing."""
    log.debug(
        json.dumps(
            {
                "event": "symbol_source_span",
                "node": node.qualified_name,
                "window": [node.start_line, last],
                "gap_lines": gaps,
            }
        )
    )


async def _span_node(
    uow: UnitOfWork, target: str, metadata: Mapping[str, Any]
) -> DocumentNode | None:
    """The tree node for ``target`` when its chunk text is only part of its span.

    None for every other target — a def chunk, a markdown heading, a text
    section and a notebook cell already hold their whole span, so they stay on
    the chunk path byte for byte. Non-Python paths opt out too: the gap markers
    would name lines of a file this renderer cannot claim to understand.
    """
    package, module = str(metadata.get("package") or ""), str(metadata.get("module") or "")
    if not package or not module:
        return None
    root = await uow.trees.load(package, module)
    node = None if root is None else _find_by_qualified_name(root, target)
    if node is None or node.kind not in SPAN_SOURCE_KINDS:
        return None
    return node if node.source_path.endswith(".py") else None


def _find_by_qualified_name(node: DocumentNode, target: str) -> DocumentNode | None:
    """Depth-first lookup of ``target`` in one document tree; None if absent."""
    if node.qualified_name == target:
        return node
    for child in node.children:
        found = _find_by_qualified_name(child, target)
        if found is not None:
            return found
    return None


async def _resolve_node_kind(uow: UnitOfWork, target: str, metadata: Mapping[str, Any]) -> str:
    """Recover the node kind for ``target`` from the document tree.

    ``str(node.kind)`` matches what the summary/tree depths emit
    (see ``lookup_service``), so items[].kind is depth-invariant. Prefers
    the chunk's own module tree (point lookup); falls back to scanning the
    package when the module key is absent. "" when nothing matches — the
    documented degrade for tree-less deployments/rows.
    """
    package = str(metadata.get("package") or "")
    if not package:
        return ""
    module = str(metadata.get("module") or "")
    if module:
        root = await uow.trees.load(package, module)
        roots: tuple[DocumentNode, ...] = () if root is None else (root,)
    else:
        roots = tuple((await uow.trees.load_all_in_package(package)).values())
    for candidate in roots:
        node = _find_by_qualified_name(candidate, target)
        if node is not None:
            return str(node.kind)
    return ""


def _source_filter(target: str, package: str | None) -> dict[str, str]:
    """Exact ``qualified_name`` match, optionally narrowed to one package."""
    if package is None:
        return {"qualified_name": target}
    return {"qualified_name": target, "package": package}


@dataclass(frozen=True, slots=True)
class SymbolSourceService:
    uow_factory: Callable[[], UnitOfWork]
    max_lines: int = _DEFAULT_MAX_LINES

    async def source_for(self, target: str) -> str:
        """Text-only façade over :meth:`source_with_items` (one run)."""
        body, _items, _extras = await self.source_with_items(target)
        return body

    async def source_with_items(
        self, target: str, *, package: str | None = None
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        """Verbatim source of ``target``. ``package`` pins the read to one
        package — the target-fallback retry passes ``__project__`` so a
        same-named dependency chunk never answers (spec 2026-09-10 P2)."""
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(filter=_source_filter(target, package), limit=1)
            tree_kind = await _resolve_node_kind(uow, target, chunks[0].metadata) if chunks else ""
            span_node = await _span_node(uow, target, chunks[0].metadata) if chunks else None
        if not chunks:
            raise NotFoundError(
                f"'{target}' has no indexed source. "
                f"{pointer_token('search', target.rsplit('.', 1)[-1])}"
            )
        chunk = chunks[0]
        path = str(chunk.metadata.get("source_path") or "")
        out = (
            _render_chunk_source(target, path, chunk.text or "", self.max_lines)
            if span_node is None
            else _render_span_source(span_node, target, path, self.max_lines)
        )
        return out, (_span_item(target, path, chunk.metadata, kind=tree_kind),), {}
