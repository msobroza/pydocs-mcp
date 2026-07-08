"""Data-access layer for the graph explorer.

``BundleReader`` is the port the service layer depends on; ``SqliteBundleReader``
is the only place that touches SQL. Every connection is ``mode=ro`` and never
routes through ``pydocs_mcp.multirepo`` (which opens read-write and can
migrate/rebuild a bundle), so reading a bundle can never mutate it.

Rows are returned as plain tuples — no domain shaping happens here. All queries
are scoped to the project's own code (package ``__project__``).
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol, runtime_checkable

_OWN = "__project__"
# Cache files are named ``{project}_{md5[:10]}.db`` (pydocs_mcp.multirepo).
_SLUG_RE = re.compile(r"^(.*)_[0-9a-f]{10}$")


def ro_uri(db: Path) -> str:
    """A read-only SQLite URI. ``Path.as_uri()`` percent-encodes %, ?, #, spaces."""
    return db.resolve().as_uri() + "?mode=ro"


@runtime_checkable
class BundleReader(Protocol):
    """Read-only access to one indexed bundle (the seam the services depend on)."""

    def reference_rows(self) -> list[tuple[str, str | None, str]]:
        """Every ``(from_node_id, to_node_id, kind)`` reference edge."""
        ...

    def references_of(self, node_id: str) -> list[tuple[str, str | None, str]]:
        """Reference edges touching ``node_id`` (either endpoint)."""
        ...

    def member_rows(self) -> list[tuple[str, str, str]]:
        """Every defined member as ``(module, name, kind)`` (kind: class | def)."""
        ...

    def find_member(self, name: str, module_part: str) -> tuple[str, str, str] | None:
        """``(name, signature, docstring)`` for a member, matching the module by
        exact or suffix (the fs-derived ``src.`` prefix reconciliation)."""
        ...

    def markdown_files(self) -> list[str]:
        """Distinct markdown file paths (``chunks.module`` for markdown sections)."""
        ...

    def markdown_sections(self, file: str) -> list[tuple[int, str]]:
        """``(chunk_id, title)`` for each section of a markdown ``file``."""
        ...

    def decisions(self) -> list[tuple[int, str]]:
        """``(chunk_id, title)`` for each recorded architectural decision."""
        ...

    def chunk(self, chunk_id: int) -> tuple[str, str] | None:
        """``(title, text)`` for one chunk (a doc section or decision body)."""
        ...

    def project_name(self) -> str:
        """The bundle's project identity."""
        ...

    def packages(self) -> list[str]:
        """Dependency package names (own code excluded)."""
        ...

    def indexed_at(self) -> float:
        """Index recency (0.0 if unstamped)."""
        ...


class SqliteBundleReader:
    """Read-only SQLite :class:`BundleReader` over a pre-built ``.db`` bundle."""

    def __init__(self, db_path: Path) -> None:
        self._db = Path(db_path)

    def _conn(self):
        return closing(sqlite3.connect(ro_uri(self._db), uri=True))

    def _scalar(self, sql: str) -> object | None:
        with self._conn() as conn:
            try:
                row = conn.execute(sql).fetchone()
            except sqlite3.OperationalError:
                return None
        return row[0] if row else None

    def reference_rows(self) -> list[tuple[str, str | None, str]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT from_node_id, to_node_id, kind FROM node_references WHERE from_package=?",
                (_OWN,),
            ).fetchall()

    def references_of(self, node_id: str) -> list[tuple[str, str | None, str]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT from_node_id, to_node_id, kind FROM node_references "
                "WHERE from_package=? AND (from_node_id=? OR to_node_id=?)",
                (_OWN, node_id, node_id),
            ).fetchall()

    def member_rows(self) -> list[tuple[str, str, str]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT module, name, kind FROM module_members WHERE package=?", (_OWN,)
            ).fetchall()

    def find_member(self, name: str, module_part: str) -> tuple[str, str, str] | None:
        with self._conn() as conn:
            return conn.execute(
                "SELECT name, signature, docstring FROM module_members "
                "WHERE package=? AND name=? AND (module=? OR module LIKE ?)",
                (_OWN, name, module_part, f"%.{module_part}"),
            ).fetchone()

    def markdown_files(self) -> list[str]:
        with self._conn() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT module FROM chunks "
                    "WHERE package=? AND origin='markdown_section' ORDER BY module",
                    (_OWN,),
                )
            ]

    def markdown_sections(self, file: str) -> list[tuple[int, str]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, title FROM chunks WHERE package=? AND origin='markdown_section' "
                "AND module=? ORDER BY id",
                (_OWN, file),
            ).fetchall()

    def decisions(self) -> list[tuple[int, str]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT id, title FROM chunks WHERE package=? AND origin='decision_record' ORDER BY id",
                (_OWN,),
            ).fetchall()

    def chunk(self, chunk_id: int) -> tuple[str, str] | None:
        with self._conn() as conn:
            return conn.execute("SELECT title, text FROM chunks WHERE id=?", (chunk_id,)).fetchone()

    def project_name(self) -> str:
        name = self._scalar("SELECT project_name FROM index_metadata LIMIT 1")
        if name:
            return str(name)
        m = _SLUG_RE.match(self._db.stem)
        return m.group(1) if m else self._db.stem

    def packages(self) -> list[str]:
        with self._conn() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM packages WHERE name != ? ORDER BY name", (_OWN,)
                )
            ]

    def indexed_at(self) -> float:
        value = self._scalar("SELECT indexed_at FROM index_metadata LIMIT 1")
        return float(value) if value is not None else 0.0
