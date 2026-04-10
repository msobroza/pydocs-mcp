"""Search functions: FTS5 for docs, LIKE for symbols."""
from __future__ import annotations

import re
import sqlite3

from pydocs_mcp.deps import normalize


def search_chunks(conn: sqlite3.Connection, query: str,
                  pkg: str | None = None, limit: int = 8) -> list[dict]:
    """BM25-ranked full-text search across all chunks."""
    words = [w for w in re.sub(r"[^\w\s]", " ", query).split() if len(w) > 1]
    if not words:
        return []

    fts = " OR ".join(words)
    sql = ("SELECT c.pkg, c.heading, c.body, c.kind, rank "
           "FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
           "WHERE chunks_fts MATCH ?")
    params: list = [fts]

    if pkg:
        sql += " AND c.pkg=?"
        params.append(normalize(pkg) if pkg != "__project__" else pkg)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def search_symbols(conn: sqlite3.Connection, query: str,
                   pkg: str | None = None, limit: int = 15) -> list[dict]:
    """Search symbols by name or docstring content."""
    pat = f"%{query.lower()}%"
    sql = "SELECT * FROM symbols WHERE (lower(name) LIKE ? OR lower(doc) LIKE ?)"
    params: list = [pat, pat]

    if pkg:
        sql += " AND pkg=?"
        params.append(normalize(pkg) if pkg != "__project__" else pkg)

    sql += " LIMIT ?"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params).fetchall()]
