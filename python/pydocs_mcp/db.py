"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 4  # v4 adds the additive ``node_references`` table + 3 indices on top of v3

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '',
        title TEXT, text TEXT, origin TEXT,
        content_hash TEXT
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package,
        content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE TABLE document_trees (
        package TEXT NOT NULL,
        module TEXT NOT NULL,
        tree_json TEXT NOT NULL,
        content_hash TEXT,
        updated_at REAL,
        PRIMARY KEY (package, module)
    );
    CREATE TABLE node_references (
        from_package   TEXT NOT NULL,
        from_node_id   TEXT NOT NULL,
        to_name        TEXT NOT NULL,
        to_node_id     TEXT,
        kind           TEXT NOT NULL,
        PRIMARY KEY (from_package, from_node_id, to_name, kind)
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_chunks_module          ON chunks(module);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
    CREATE INDEX ix_refs_from              ON node_references(from_package, from_node_id);
    CREATE INDEX ix_refs_to_name           ON node_references(to_name);
    CREATE INDEX ix_refs_to_node           ON node_references(to_node_id);
"""

# Tables we know about — dropped on a version mismatch so earlier schemas
# (including the pre-v2 `symbols` table) are cleared before recreating.
_KNOWN_TABLES = (
    "chunks_fts", "chunks", "module_members", "packages", "symbols",
    "document_trees",
    "node_references",
)


def cache_path_for_project(project_dir: Path) -> Path:
    """Return the per-project SQLite cache file path under ``CACHE_DIR``.

    Each project gets its own ``.db`` file derived from its absolute path,
    so multiple projects never share state.
    """
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def _drop_all_known_tables(connection: sqlite3.Connection) -> None:
    for tbl in _KNOWN_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {tbl}")


def _try_add_column(conn: sqlite3.Connection, table: str, column_ddl: str) -> None:
    """ALTER TABLE ADD COLUMN that tolerates the column already existing.

    Used by the idempotent v3 additions sweep. SQLite raises
    ``OperationalError`` with ``duplicate column name`` when the column is
    already present; we swallow that case so the sweep is safe to re-run.
    Any other ``OperationalError`` propagates (real schema damage).
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _apply_v3_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v3 shape.

    Adds the ``document_trees`` table, its index, and the new columns
    (``chunks.module``, ``chunks.content_hash``, ``packages.local_path``).
    Every operation tolerates pre-existing state — ``CREATE ... IF NOT
    EXISTS`` for the table/index, ``_try_add_column`` swallowing duplicate-
    column errors for ALTERs. Used both as the v2→v3 forward migration
    and as a v3-on-open repair sweep.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS document_trees ("
        "package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL, "
        "content_hash TEXT, updated_at REAL, PRIMARY KEY (package, module))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package)"
    )
    _try_add_column(conn, "chunks", "module TEXT DEFAULT ''")
    _try_add_column(conn, "chunks", "content_hash TEXT")
    _try_add_column(conn, "packages", "local_path TEXT")
    # The fresh-DB DDL also creates ix_chunks_module on the new ``chunks.module``
    # column. v2->v3 in-place migration adds the column via _try_add_column but
    # previously omitted the index — queries that filter chunks by module on a
    # migrated DB hit a full table scan until the next fresh rebuild.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_module ON chunks(module)"
    )


def _apply_v4_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v4 shape.

    Mirrors :func:`_apply_v3_additions` — ``CREATE TABLE IF NOT EXISTS``
    + ``CREATE INDEX IF NOT EXISTS``; no destructive drops. Used both as
    the v3 → v4 forward migration AND as a v4-on-open repair sweep
    (drift recovery, AC #3).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_references ("
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, "
        "to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (from_package, from_node_id, to_name, kind))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_from "
        "ON node_references(from_package, from_node_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)"
    )


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, migrating or rebuilding per user_version.

    - v4 already: re-run v4 sweep (additive, idempotent; drift recovery).
    - v3 → v4: additive forward migration (CREATE TABLE node_references
      + 3 indices); rows in all existing tables survive.
    - v2 → v3 → v4: walk both forward migrations in order.
    - Any other mismatch: drop every known table and recreate from current DDL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current == SCHEMA_VERSION:
        # v4 — re-run additive sweep for drift recovery.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
    elif current == 3:
        # v3 → v4 — additive. We also re-run the v3 sweep first because some
        # legacy v3-stamped DBs (rebase artefacts between sub-PR #5 and #6)
        # lack ``document_trees`` / ``content_hash`` / ``local_path`` despite
        # the stamp; rerunning the idempotent v3 sweep repairs that drift
        # before stamping forward to v4.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current == 2:
        # v2 → v3 → v4 — walk both forward migrations in order.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    else:
        _drop_all_known_tables(conn)
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all rows for a package across chunks, members, trees, refs, packages.

    Sub-PR #5b adds ``node_references`` to the per-package sweep — without
    this, stale refs survive a re-index and ``ref_svc.callers(...)`` returns
    references to deleted source nodes.
    """
    connection.execute("DELETE FROM chunks  WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM document_trees WHERE package=?", (package_name,))
    connection.execute("DELETE FROM node_references WHERE from_package=?", (package_name,))
    connection.execute("DELETE FROM packages WHERE name=?", (package_name,))


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every indexed package across all five entity tables."""
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
    connection.execute("DELETE FROM document_trees")
    connection.execute("DELETE FROM node_references")
    connection.commit()


def rebuild_fulltext_index(connection: sqlite3.Connection) -> None:
    """Rebuild the FTS5 index after bulk writes so new rows become searchable."""
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    connection.commit()


def get_stored_content_hash(
    connection: sqlite3.Connection, package_name: str
) -> str | None:
    """Return the stored content hash for a package, or ``None`` if not indexed."""
    row = connection.execute(
        "SELECT content_hash FROM packages WHERE name=?", (package_name,)
    ).fetchone()
    return row["content_hash"] if row else None


from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider  # noqa: E402


def build_connection_provider(cache_path: Path):
    """Factory — returns the default ConnectionProvider for a given DB path."""
    return PerCallConnectionProvider(cache_path=cache_path)
