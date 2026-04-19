"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 2

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        title TEXT, text TEXT, origin TEXT
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
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
"""

# Tables we know about — dropped on a version mismatch so legacy schemas
# (including the pre-v2 `symbols` table) are cleared before recreating.
_KNOWN_TABLES = ("chunks_fts", "chunks", "module_members", "packages", "symbols")


def db_path_for(project_dir: Path) -> Path:
    """Each project gets its own .db file based on its absolute path.

    NOTE: Task 13 renames this to `cache_path_for_project`.
    """
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def _drop_all_known_tables(conn: sqlite3.Connection) -> None:
    for tbl in _KNOWN_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, rebuilding if PRAGMA user_version differs.

    A mismatch drops every known table (including the pre-v2 `symbols` table)
    and recreates the schema from the current DDL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        _drop_all_known_tables(conn)
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


# Thin shim; Task 13 deletes this once all callers migrate to open_index_database.
def open_db(path: Path) -> sqlite3.Connection:
    """(Deprecated) Alias for open_index_database. Task 13 removes this shim."""
    return open_index_database(path)


# TEMPORARY: old helpers with OLD column names kept alive until callers migrate.
# These reference old columns (pkg, heading, body, hash, etc.) which NO LONGER
# exist in v2 — so these functions are intentionally broken until Task 15–18
# rewrites each caller to use the new schema. Tests that hit these functions
# will fail until Task 19's mechanical test updates.

def clear_pkg(conn: sqlite3.Connection, name: str):
    """Remove all data for a package."""
    conn.execute("DELETE FROM chunks  WHERE package=?", (name,))
    conn.execute("DELETE FROM module_members WHERE package=?", (name,))
    conn.execute("DELETE FROM packages WHERE name=?", (name,))


def clear_all(conn: sqlite3.Connection):
    """Clear the entire database."""
    conn.execute("DELETE FROM packages")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM module_members")
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection):
    """Rebuild the FTS index after bulk writes."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()


def get_cached_hash(conn: sqlite3.Connection, name: str) -> str | None:
    """Get the stored hash for a package, or None if not indexed."""
    row = conn.execute("SELECT content_hash FROM packages WHERE name=?", (name,)).fetchone()
    return row["content_hash"] if row else None
