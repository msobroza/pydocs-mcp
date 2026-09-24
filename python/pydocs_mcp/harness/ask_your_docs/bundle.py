"""Data-access layer for the graph explorer.

``BundleReader`` is the port the service layer depends on; ``SqliteBundleReader``
is the only place that touches SQL. Every connection is ``mode=ro`` and never
routes through ``pydocs_mcp.multirepo`` (which opens read-write and can
migrate/rebuild a bundle), so reading a bundle can never mutate it.

Rows are returned as plain tuples — no domain shaping happens here. All queries
are scoped to the project's own code (package ``__project__``) and, on a bundle
holding several branches, to the served default branch (#313).
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydocs_mcp.db_branch_key_migration import DEFAULT_BRANCH_NAME_SQL
from pydocs_mcp.models import BranchSlice, BranchStatus
from pydocs_mcp.storage.sqlite.table_crud import branch_read_clause

_OWN = "__project__"
# Cache files are named ``{project}_{md5[:10]}.db`` (pydocs_mcp.multirepo).
_SLUG_RE = re.compile(r"^(.*)_[0-9a-f]{10}$")
# A landing unit is stored as a branches row named by its 40-hex sha
# (multi-branch spec §6.5b); v18 also stamps landing_kind.
_LANDING_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class IndexedBranch:
    """One ``branches`` row of a bundle (schema v16+), read-only."""

    name: str
    head_sha: str
    base_name: str | None
    is_default: bool
    status: BranchStatus
    merged_into: str | None
    landing_kind: str | None
    indexed_at: float

    @property
    def is_landing_unit(self) -> bool:
        return self.landing_kind is not None or bool(_LANDING_SHA_RE.match(self.name))


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    """Column names of ``table``; empty when the table does not exist.

    PRAGMA table_info yields no rows (and no error) for a missing table, which is
    how callers tell a pre-v16 bundle apart from one with a wrong-shaped table.
    ``table`` is never user input — every call site passes a literal.
    """
    return frozenset(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))


def _indexed_branch(row: tuple) -> IndexedBranch:
    name, head_sha, base_name, is_default, status, merged_into, landing_kind, indexed_at = row
    return IndexedBranch(
        name=str(name),
        head_sha=str(head_sha),
        base_name=base_name,
        is_default=bool(is_default),
        status=BranchStatus(status),
        merged_into=merged_into,
        landing_kind=landing_kind,
        indexed_at=float(indexed_at or 0.0),
    )


# #313: on a bundle holding several branches the page reads the served default
# branch — the one its caption names — never every branch's rows drawn as one
# graph. Its branch selector (U1, once every tool takes ``branch``) does not
# drive these reads yet. The tree tier keeps its read rule (the default branch
# plus the dependency tier); a chunk is the default branch's when its tree-slice
# membership says so — raw SQL, as everywhere on this page, over the one slice
# value the product's pin reads (``filter_helpers.chunk_branch_pin_fields``).
_SERVED_CHUNK_ROW = (
    " AND EXISTS (SELECT 1 FROM branch_chunks bc WHERE bc.chunk_id = chunks.id"  # noqa: S608 — the only interpolations are module constants
    f" AND bc.branch = ({DEFAULT_BRANCH_NAME_SQL}) AND bc.slice = '{BranchSlice.TREE.value}')"
)


def _holds_several_branches(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("SELECT COUNT(*) FROM branches").fetchone()
    except sqlite3.OperationalError:
        return False  # pre-v16: no branch dimension
    return bool(row and row[0] > 1)


def _served_branch_scope(conn: sqlite3.Connection, table: str) -> tuple[str, tuple[object, ...]]:
    """``(" AND <predicate>", params)`` reading ``table``'s served-branch rows,
    or ``("", ())`` — today's SQL — on a one-branch or pre-v18 bundle.
    ``table`` is never user input: every call site passes a literal."""
    if not _holds_several_branches(conn):
        return "", ()
    if table == "chunks":
        return _SERVED_CHUNK_ROW, ()
    if "branch" not in _table_columns(conn, table):
        return "", ()
    return f" AND {branch_read_clause()}", (None,)


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

    def branches(self) -> tuple[IndexedBranch, ...]:
        """Every ``branches`` row, default first then by name; ``()`` before v16."""
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

    def _served(
        self, table: str, sql: str, params: tuple[object, ...], order: str = ""
    ) -> list[tuple]:
        """``sql`` over ``table``'s served-branch rows (#313), then ``order``."""
        with self._conn() as conn:
            scope, scope_params = _served_branch_scope(conn, table)
            return conn.execute(sql + scope + order, (*params, *scope_params)).fetchall()

    def reference_rows(self) -> list[tuple[str, str | None, str]]:
        return self._served(
            "node_references",
            "SELECT from_node_id, to_node_id, kind FROM node_references WHERE from_package=?",
            (_OWN,),
        )

    def references_of(self, node_id: str) -> list[tuple[str, str | None, str]]:
        return self._served(
            "node_references",
            "SELECT from_node_id, to_node_id, kind FROM node_references "
            "WHERE from_package=? AND (from_node_id=? OR to_node_id=?)",
            (_OWN, node_id, node_id),
        )

    def member_rows(self) -> list[tuple[str, str, str]]:
        return self._served(
            "module_members",
            "SELECT module, name, kind FROM module_members WHERE package=?",
            (_OWN,),
        )

    def find_member(self, name: str, module_part: str) -> tuple[str, str, str] | None:
        rows = self._served(
            "module_members",
            "SELECT name, signature, docstring FROM module_members "
            "WHERE package=? AND name=? AND (module=? OR module LIKE ?)",
            (_OWN, name, module_part, f"%.{module_part}"),
            " LIMIT 1",
        )
        return rows[0] if rows else None

    def markdown_files(self) -> list[str]:
        rows = self._served(
            "chunks",
            "SELECT DISTINCT module FROM chunks WHERE package=? AND origin='markdown_section'",
            (_OWN,),
            " ORDER BY module",
        )
        return [row[0] for row in rows]

    def markdown_sections(self, file: str) -> list[tuple[int, str]]:
        return self._served(
            "chunks",
            "SELECT id, title FROM chunks WHERE package=? AND origin='markdown_section' "
            "AND module=?",
            (_OWN, file),
            " ORDER BY id",
        )

    def decisions(self) -> list[tuple[int, str]]:
        return self._served(
            "chunks",
            "SELECT id, title FROM chunks WHERE package=? AND origin='decision_record'",
            (_OWN,),
            " ORDER BY id",
        )

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

    def branches(self) -> tuple[IndexedBranch, ...]:
        with self._conn() as conn:
            columns = _table_columns(conn, "branches")
            if not columns:
                return ()  # pre-v16 bundle: no table, no rows
            # landing_kind arrives with schema v18; read NULL on v16.
            landing = "landing_kind" if "landing_kind" in columns else "NULL"
            rows = conn.execute(
                "SELECT name, head_sha, base_name, is_default, status, merged_into, "  # noqa: S608 — the only interpolation is a closed two-value column choice
                f"{landing}, indexed_at FROM branches ORDER BY is_default DESC, name"
            ).fetchall()
        return tuple(_indexed_branch(row) for row in rows)
