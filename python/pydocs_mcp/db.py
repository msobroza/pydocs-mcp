"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 3

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
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
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
"""

# Tables we know about — dropped on a version mismatch so earlier schemas
# (including the pre-v2 `symbols` table) are cleared before recreating.
_KNOWN_TABLES = (
    "chunks_fts", "chunks", "module_members", "packages", "symbols",
    "document_trees",
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

    Used by the soft v2→v3 migration. SQLite raises ``OperationalError``
    with ``duplicate column name`` when the column is already present;
    we swallow that case so the migration is idempotent. Any other
    ``OperationalError`` propagates (real schema damage).
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Soft-migrate a v2 database to v3 in place.

    Preserves existing rows in ``packages``, ``chunks``, ``module_members``,
    and ``chunks_fts``. Adds the new ``document_trees`` table and the two
    new columns (``chunks.content_hash``, ``packages.local_path``) without
    rewriting unchanged data.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS document_trees ("
        "package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL, "
        "content_hash TEXT, updated_at REAL, PRIMARY KEY (package, module))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package)"
    )
    _try_add_column(conn, "chunks", "content_hash TEXT")
    _try_add_column(conn, "packages", "local_path TEXT")


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, migrating or rebuilding per user_version.

    - v3 already: no-op.
    - v2 → v3: soft migration (CREATE document_trees + ALTER two columns);
      rows in ``packages`` / ``chunks`` / ``module_members`` survive.
    - Any other mismatch: drop every known table and recreate from current DDL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        if current == 2:
            _migrate_v2_to_v3(conn)
        else:
            _drop_all_known_tables(conn)
            conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all rows for a package across chunks, members, and packages."""
    connection.execute("DELETE FROM chunks  WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM packages WHERE name=?", (package_name,))


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every indexed package: packages, chunks, and module members."""
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
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
