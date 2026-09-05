"""Verbatim source for one indexed symbol — get_symbol(depth="source") (spec §D1/§D7).

The §D7 recovery chain terminates here: a truncated card points at
get_symbol(..., depth="source"), and if even one symbol exceeds the line cap
the rendered file path is the final, always-valid recovery step (readable by
the agent's own file tools).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

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


@dataclass(frozen=True, slots=True)
class SymbolSourceService:
    uow_factory: Callable[[], UnitOfWork]
    max_lines: int = _DEFAULT_MAX_LINES

    async def source_for(self, target: str) -> str:
        """Text-only façade over :meth:`source_with_items` (one run)."""
        body, _items, _extras = await self.source_with_items(target)
        return body

    async def source_with_items(
        self, target: str
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(filter={"qualified_name": target}, limit=1)
            tree_kind = await _resolve_node_kind(uow, target, chunks[0].metadata) if chunks else ""
        if not chunks:
            raise NotFoundError(
                f"'{target}' has no indexed source. "
                f"{pointer_token('search', target.rsplit('.', 1)[-1])}"
            )
        chunk = chunks[0]
        path = str(chunk.metadata.get("source_path") or "")
        lines = (chunk.text or "").splitlines()
        shown = lines[: self.max_lines]
        header = f"# Source — `{target}`" + (f"  ·  {path}" if path else "")
        body = "\n".join(shown)
        out = f"{header}\n\n```python\n{body}\n```\n"
        if len(lines) > self.max_lines:
            elided = len(lines) - self.max_lines
            out += f"[… {elided} more lines — read {path or 'the source file'} directly]\n"
            ledger = get_active_ledger()
            if ledger is not None:
                ledger.record(
                    TruncationEntry(
                        description=f"{elided} source lines beyond the {self.max_lines}-line cap",
                        recovery="",  # the inline file path IS the terminal recovery
                    )
                )
        return out, (_span_item(target, path, chunk.metadata, kind=tree_kind),), {}
