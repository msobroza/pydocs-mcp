"""SQLite database with FTS5 full-text search."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"


def db_path_for(project_dir: Path) -> Path:
    """Each project gets its own .db file based on its absolute path."""
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the database with all tables."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, requires TEXT, hash TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY, pkg TEXT,
            heading TEXT, body TEXT, kind TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            heading, body, pkg,
            content=chunks, content_rowid=id,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, pkg TEXT, module TEXT,
            name TEXT, kind TEXT, signature TEXT,
            returns TEXT, params TEXT, doc TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_c  ON chunks(pkg);
        CREATE INDEX IF NOT EXISTS ix_sp ON symbols(pkg);
        CREATE INDEX IF NOT EXISTS ix_sn ON symbols(name);
    """)
    conn.commit()
    return conn


def clear_pkg(conn: sqlite3.Connection, name: str):
    """Remove all data for a package."""
    conn.execute("DELETE FROM chunks  WHERE pkg=?", (name,))
    conn.execute("DELETE FROM symbols WHERE pkg=?", (name,))
    conn.execute("DELETE FROM packages WHERE name=?", (name,))


def clear_all(conn: sqlite3.Connection):
    """Clear the entire database."""
    for table in ("packages", "chunks", "symbols"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection):
    """Rebuild the FTS index after bulk writes."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()


def get_cached_hash(conn: sqlite3.Connection, name: str) -> str | None:
    """Get the stored hash for a package, or None if not indexed."""
    row = conn.execute("SELECT hash FROM packages WHERE name=?", (name,)).fetchone()
    return row["hash"] if row else None
