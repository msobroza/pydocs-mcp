"""SQLite overlay store for cross-repo links (spec 2026-07-11 §3.1-§3.2, §A1.1).

The overlay is a single file NEXT TO the bundles, never inside them, and is a
pure derivative: on a ``user_version`` mismatch we drop and recreate — a
relink, never a reindex (unlike bundles, nothing of value is lost). It is
deliberately NOT a ``PerCallConnectionProvider`` consumer: that provider
refuses missing files (``CacheNotIndexedError``) and the overlay's write path
must create-if-missing; ``replace_edges_touching`` is its own transaction
boundary (there is no multi-bundle UnitOfWork by design).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.cross_link_edge import (
    CrossLinkEdge,
    LinkedBundleStamp,
    WorkspaceNodeScore,
)

# The overlay's OWN schema version (independent of the bundle SCHEMA_VERSION).
# Mismatch policy: drop and relink — the overlay is disposable (spec §3.1).
_LINKS_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE cross_references (
    from_project TEXT NOT NULL,
    from_package TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_project   TEXT NOT NULL,
    to_node_id   TEXT NOT NULL,
    to_name      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    PRIMARY KEY (from_project, from_node_id, to_project, to_node_id, kind)
);
CREATE INDEX ix_xrefs_to   ON cross_references(to_project, to_node_id);
CREATE INDEX ix_xrefs_from ON cross_references(from_project, from_node_id);

CREATE TABLE linked_bundles (
    bundle_stem  TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    bundle_path  TEXT NOT NULL,
    indexed_at   REAL NOT NULL,
    git_head     TEXT,
    linked_at    REAL NOT NULL
);

CREATE TABLE workspace_node_scores (
    project        TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    pagerank       REAL,
    in_degree      INTEGER NOT NULL,
    PRIMARY KEY (project, qualified_name)
);
"""


@dataclass(frozen=True, slots=True)
class SqliteCrossLinkStore:
    """Persisted ``CrossLinkStore`` over the workspace overlay sidecar."""

    path: Path

    async def edges_into(
        self,
        to_project: str,
        to_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        return await asyncio.to_thread(
            self._select_edges,
            "to_project = ? AND to_node_id = ?",
            (to_project, to_node_id),
            kinds,
            limit,
        )

    async def edges_from(
        self,
        from_project: str,
        from_node_id: str,
        *,
        kinds: tuple[ReferenceKind, ...] | None = None,
        limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]:
        return await asyncio.to_thread(
            self._select_edges,
            "from_project = ? AND from_node_id = ?",
            (from_project, from_node_id),
            kinds,
            limit,
        )

    async def replace_edges_touching(self, project: str, edges: tuple[CrossLinkEdge, ...]) -> None:
        await asyncio.to_thread(self._replace_edges_touching, project, edges)

    async def bundle_stamps(self) -> tuple[LinkedBundleStamp, ...]:
        return await asyncio.to_thread(self._bundle_stamps)

    async def stamp_bundle(self, stamp: LinkedBundleStamp) -> None:
        await asyncio.to_thread(self._stamp_bundle, stamp)

    async def delete_stamp(self, bundle_stem: str) -> None:
        await asyncio.to_thread(self._delete_stamp, bundle_stem)

    async def replace_workspace_scores(self, rows: tuple[WorkspaceNodeScore, ...]) -> None:
        await asyncio.to_thread(self._replace_workspace_scores, rows)

    async def workspace_scores_for(
        self, pairs: tuple[tuple[str, str], ...]
    ) -> Mapping[tuple[str, str], WorkspaceNodeScore]:
        return await asyncio.to_thread(self._workspace_scores_for, pairs)

    # ------------------------------------------------------------------ #
    # Blocking internals (run under asyncio.to_thread per house style)
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        """Open the overlay, creating or resetting its schema as needed.

        Version mismatch drops every table and recreates (relink-not-migrate,
        AC3) — bundle files are never touched by construction.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != _LINKS_SCHEMA_VERSION:
            self._reset_schema(conn)
        return conn

    def _reset_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for row in rows:
            conn.execute(f'DROP TABLE IF EXISTS "{row["name"]}"')
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {_LINKS_SCHEMA_VERSION}")
        conn.commit()

    def _select_edges(
        self,
        where: str,
        params: tuple[str, ...],
        kinds: tuple[ReferenceKind, ...] | None,
        limit: int,
    ) -> tuple[CrossLinkEdge, ...]:
        sql = f"SELECT * FROM cross_references WHERE {where}"
        bound: list[object] = list(params)
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            sql += f" AND kind IN ({placeholders})"
            bound += [str(k) for k in kinds]
        sql += " ORDER BY from_project, from_node_id, to_project, to_node_id, kind LIMIT ?"
        bound.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, bound).fetchall()
        return tuple(_edge_from_row(row) for row in rows)

    def _replace_edges_touching(self, project: str, edges: tuple[CrossLinkEdge, ...]) -> None:
        with self._connect() as conn:
            try:
                conn.execute(
                    "DELETE FROM cross_references WHERE from_project = ? OR to_project = ?",
                    (project, project),
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO cross_references"
                    " (from_project, from_package, from_node_id,"
                    "  to_project, to_node_id, to_name, kind)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            e.from_project,
                            e.from_package,
                            e.from_node_id,
                            e.to_project,
                            e.to_node_id,
                            e.to_name,
                            str(e.kind),
                        )
                        for e in edges
                    ],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _bundle_stamps(self) -> tuple[LinkedBundleStamp, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM linked_bundles ORDER BY bundle_stem").fetchall()
        return tuple(
            LinkedBundleStamp(
                bundle_stem=row["bundle_stem"],
                project_name=row["project_name"],
                bundle_path=row["bundle_path"],
                indexed_at=row["indexed_at"],
                git_head=row["git_head"],
                linked_at=row["linked_at"],
            )
            for row in rows
        )

    def _stamp_bundle(self, stamp: LinkedBundleStamp) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO linked_bundles"
                " (bundle_stem, project_name, bundle_path, indexed_at, git_head, linked_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    stamp.bundle_stem,
                    stamp.project_name,
                    stamp.bundle_path,
                    stamp.indexed_at,
                    stamp.git_head,
                    stamp.linked_at,
                ),
            )
            conn.commit()

    def _delete_stamp(self, bundle_stem: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM linked_bundles WHERE bundle_stem = ?", (bundle_stem,))
            conn.commit()

    def _replace_workspace_scores(self, rows: tuple[WorkspaceNodeScore, ...]) -> None:
        with self._connect() as conn:
            try:
                conn.execute("DELETE FROM workspace_node_scores")
                conn.executemany(
                    "INSERT INTO workspace_node_scores"
                    " (project, qualified_name, pagerank, in_degree)"
                    " VALUES (?, ?, ?, ?)",
                    [(r.project, r.qualified_name, r.pagerank, r.in_degree) for r in rows],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _workspace_scores_for(
        self, pairs: tuple[tuple[str, str], ...]
    ) -> dict[tuple[str, str], WorkspaceNodeScore]:
        if not pairs:
            return {}
        out: dict[tuple[str, str], WorkspaceNodeScore] = {}
        with self._connect() as conn:
            for project, qname in pairs:
                row = conn.execute(
                    "SELECT * FROM workspace_node_scores WHERE project = ? AND qualified_name = ?",
                    (project, qname),
                ).fetchone()
                if row is not None:
                    out[(project, qname)] = WorkspaceNodeScore(
                        project=row["project"],
                        qualified_name=row["qualified_name"],
                        pagerank=row["pagerank"],
                        in_degree=row["in_degree"],
                    )
        return out


def _edge_from_row(row: sqlite3.Row) -> CrossLinkEdge:
    return CrossLinkEdge(
        from_project=row["from_project"],
        from_package=row["from_package"],
        from_node_id=row["from_node_id"],
        to_project=row["to_project"],
        to_node_id=row["to_node_id"],
        to_name=row["to_name"],
        kind=ReferenceKind(row["kind"]),
    )
