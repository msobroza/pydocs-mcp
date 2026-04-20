"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)

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

# Tables we know about — dropped on a version mismatch so earlier schemas
# (including the pre-v2 `symbols` table) are cleared before recreating.
_KNOWN_TABLES = ("chunks_fts", "chunks", "module_members", "packages", "symbols")


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


# ── Chunk ↔ row ──────────────────────────────────────────────────────────
def _chunk_to_row(c: Chunk) -> dict[str, object]:
    md = c.metadata
    return {
        "id": c.id,
        "package": md.get(ChunkFilterField.PACKAGE.value, ""),
        "title":   md.get(ChunkFilterField.TITLE.value, ""),
        "text":    c.text,
        "origin":  md.get(ChunkFilterField.ORIGIN.value, ""),
    }


def _row_to_chunk(row) -> Chunk:
    def _get(key: str):
        # row may be a sqlite3.Row or a plain dict; both support __getitem__
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = _get(key)
        if value:
            metadata[key] = value
    return Chunk(
        text=_get("text") or "",
        id=_get("id"),
        metadata=metadata,
    )


# ── ModuleMember ↔ row ───────────────────────────────────────────────────
def _module_member_to_row(m: ModuleMember) -> dict[str, object]:
    md = m.metadata
    params = md.get("parameters", ())
    params_json = json.dumps(
        [
            {"name": p.name, "annotation": p.annotation, "default": p.default}
            if isinstance(p, Parameter)
            else p
            for p in params
        ]
    )
    return {
        "id": m.id,
        "package":           md.get(ModuleMemberFilterField.PACKAGE.value, ""),
        "module":            md.get(ModuleMemberFilterField.MODULE.value, ""),
        "name":              md.get(ModuleMemberFilterField.NAME.value, ""),
        "kind":              md.get(ModuleMemberFilterField.KIND.value, ""),
        "signature":         md.get("signature", ""),
        "return_annotation": md.get("return_annotation", ""),
        "parameters":        params_json,
        "docstring":         md.get("docstring", ""),
    }


def _row_to_module_member(row) -> ModuleMember:
    def _get(key: str):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    raw_params = json.loads(_get("parameters") or "[]")
    params = tuple(
        Parameter(
            name=p["name"],
            annotation=p.get("annotation", ""),
            default=p.get("default", ""),
        )
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: _get("package") or "",
        ModuleMemberFilterField.MODULE.value:  _get("module") or "",
        ModuleMemberFilterField.NAME.value:    _get("name") or "",
        ModuleMemberFilterField.KIND.value:    _get("kind") or "",
        "signature":         _get("signature") or "",
        "return_annotation": _get("return_annotation") or "",
        "parameters":        params,
        "docstring":         _get("docstring") or "",
    }
    return ModuleMember(id=_get("id"), metadata=metadata)


# ── Package ↔ row ────────────────────────────────────────────────────────
def _package_to_row(pkg: Package) -> dict[str, object]:
    return {
        "name": pkg.name,
        "version": pkg.version,
        "summary": pkg.summary,
        "homepage": pkg.homepage,
        "dependencies": json.dumps(list(pkg.dependencies)),
        "content_hash": pkg.content_hash,
        "origin": pkg.origin.value,
    }


def _row_to_package(row) -> Package:
    def _get(key: str):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    return Package(
        name=_get("name") or "",
        version=_get("version") or "",
        summary=_get("summary") or "",
        homepage=_get("homepage") or "",
        dependencies=tuple(json.loads(_get("dependencies") or "[]")),
        content_hash=_get("content_hash") or "",
        origin=PackageOrigin(_get("origin") or PackageOrigin.DEPENDENCY.value),
    )


from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider  # noqa: E402


def build_connection_provider(cache_path: Path):
    """Factory — returns the default ConnectionProvider for a given DB path."""
    return PerCallConnectionProvider(cache_path=cache_path)
