"""SqliteDocumentTreeStore — per-module DocumentNode trees as JSON rows (spec §12.2)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire


@dataclass(frozen=True, slots=True)
class SqliteDocumentTreeStore:
    """DocumentTreeStore backed by the ``document_trees`` SQLite table (spec §12.2).

    Each row stores one module's tree as a JSON blob keyed by
    ``(package, module)``. The ``module`` column equals the root
    ``DocumentNode.qualified_name`` — callers (``IndexingService``) own
    that identity mapping and pass ``package`` explicitly so the store
    never introspects each tree to infer which package it belongs to.
    """

    provider: ConnectionProvider

    async def save_many(
        self,
        trees: Sequence[DocumentNode],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None:
        if not trees:
            return
        # Capture the write timestamp once per call so every tree in a
        # batch shares a consistent ``updated_at`` (cheaper + clearer than
        # asking time.time() per row).
        now = time.time()
        rows = [
            (
                package,
                t.qualified_name,
                _serialize_tree_to_json(t),
                t.content_hash,
                now,
            )
            for t in trees
        ]
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.executemany,
                "INSERT INTO document_trees "
                "(package, module, tree_json, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(package, module) DO UPDATE SET "
                "tree_json=excluded.tree_json, "
                "content_hash=excluded.content_hash, "
                "updated_at=excluded.updated_at",
                rows,
            )

    async def load(self, package: str, module: str) -> DocumentNode | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT tree_json FROM document_trees WHERE package=? AND module=?",
                    (package, module),
                ).fetchone()
            )
        return _deserialize_tree_from_json(row[0]) if row else None

    async def load_all_in_package(self, package: str) -> dict[str, DocumentNode]:
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT module, tree_json FROM document_trees WHERE package=?",
                    (package,),
                ).fetchall()
            )
        return {r["module"]: _deserialize_tree_from_json(r["tree_json"]) for r in rows}

    async def exists(self, package: str, module: str) -> bool:
        """Cheap existence check — no JSON parse, no DocumentNode allocation.

        Used by ``LookupService._longest_indexed_module`` to probe dotted-
        prefix candidates without paying the full deserialization cost; the
        downstream ``_module_lookup`` / ``_symbol_lookup`` paths still call
        ``load`` once on the winning candidate.
        """
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT 1 FROM document_trees WHERE package=? AND module=? LIMIT 1",
                    (package, module),
                ).fetchone()
            )
        return row is not None

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM document_trees WHERE package=?",
                (package,),
            )

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "DELETE FROM document_trees",
            )


def _serialize_tree_to_json(node: DocumentNode) -> str:
    """Serialise a ``DocumentNode`` tree to compact JSON for storage."""
    return json.dumps(_node_to_dict(node), separators=(",", ":"))


def _node_to_dict(node: DocumentNode) -> dict:
    return {
        "node_id": node.node_id,
        "qualified_name": node.qualified_name,
        "title": node.title,
        "kind": node.kind.value,
        "source_path": node.source_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "text": node.text,
        "content_hash": node.content_hash,
        "summary": node.summary,
        "extra_metadata": dict(node.extra_metadata),
        "parent_id": node.parent_id,
        "children": [_node_to_dict(c) for c in node.children],
    }


def _deserialize_tree_from_json(s: str) -> DocumentNode:
    return _dict_to_node(json.loads(s))


def _dict_to_node(d: dict) -> DocumentNode:
    return DocumentNode(
        node_id=d["node_id"],
        qualified_name=d["qualified_name"],
        title=d["title"],
        kind=NodeKind(d["kind"]),
        source_path=d["source_path"],
        start_line=d["start_line"],
        end_line=d["end_line"],
        text=d["text"],
        content_hash=d["content_hash"],
        summary=d.get("summary", ""),
        extra_metadata=d.get("extra_metadata", {}),
        parent_id=d.get("parent_id"),
        children=tuple(_dict_to_node(c) for c in d.get("children", ())),
    )
