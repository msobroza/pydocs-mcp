"""Verbatim source for one indexed symbol — get_symbol(depth="source") (spec §D1/§D7).

The §D7 recovery chain terminates here: a truncated card points at
get_symbol(..., depth="source"), and if even one symbol exceeds the line cap
the rendered file path is the final, always-valid recovery step (readable by
the agent's own file tools).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_MAX_LINES = 400


def _span_item(target: str, path: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """The single §3.3 row for the rendered span (contract, Task 6).

    Chunk rows persist qualified_name + the v15 span but no node kind — so
    ``kind`` degrades to "" here rather than pretending to know; legacy
    (pre-v15) rows likewise degrade path/span to null instead of failing.
    """
    start = metadata.get("start_line")
    end = metadata.get("end_line")
    qname = str(metadata.get("qualified_name") or target)
    return {
        "node_id": qname,
        "kind": str(metadata.get("kind") or ""),
        "qualified_name": qname,
        "path": path or None,
        "start_line": start if isinstance(start, int) else None,
        "end_line": end if isinstance(end, int) else None,
    }


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
        return out, (_span_item(target, path, chunk.metadata),), {}
