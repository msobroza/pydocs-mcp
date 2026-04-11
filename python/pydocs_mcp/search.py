"""Search functions: FTS5 for docs, LIKE for symbols."""
from __future__ import annotations

import re
import sqlite3

from pydocs_mcp.deps import normalize


def search_chunks(
    conn: sqlite3.Connection,
    query: str,
    pkg: str | None = None,
    limit: int = 8,
    internal: bool | None = None,
    topic: str | None = None,
) -> list[dict]:
    """BM25 full-text search over indexed chunks.

    Args:
        query: Space-separated search terms; words are joined with OR for FTS5 matching.
        pkg: Restrict to a specific package name. '__project__' is matched literally.
        internal: True → project source only; False → dependencies only; None → all.
        topic: If given, restricts results to chunks whose heading contains this string (LIKE).
        limit: Maximum number of results.
    """
    # If the query already uses FTS operators, pass it through directly.
    # Otherwise split into words and join with OR for broad matching.
    _FTS_OPS = {"OR", "AND", "NOT"}
    tokens = query.split()
    if any(t in _FTS_OPS for t in tokens):
        fts_q = query
    else:
        words = [w for w in tokens if len(w) > 1]
        if not words:
            return []
        fts_q = " OR ".join(words)

    # Build WHERE clauses and params incrementally
    where: list[str] = ["chunks_fts MATCH ?"]
    params: list = [fts_q]

    if pkg is not None:
        lit = pkg if pkg == "__project__" else normalize(pkg)
        where.append("c.pkg = ?")
        params.append(lit)

    if internal is True:
        where.append("c.pkg = '__project__'")
    elif internal is False:
        where.append("c.pkg != '__project__'")

    if topic:
        # Escape SQL LIKE wildcards so the topic is matched literally, not as a pattern
        escaped_topic = topic.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("c.heading LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_topic}%")

    params.append(limit)
    sql = (
        "SELECT c.pkg, c.heading, c.body, c.kind, -m.rank AS rank"
        " FROM chunks_fts m JOIN chunks c ON c.id = m.rowid"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def search_symbols(
    conn,
    query: str,
    pkg: str | None = None,
    limit: int = 15,
    internal: bool | None = None,
) -> list[dict]:
    """Symbol LIKE search on name and docstring.

    Args:
        query: Fragment to match against symbol name or docstring (case-insensitive LIKE).
        pkg: Restrict to a specific package name. '__project__' is matched literally.
        internal: True → project source only; False → dependencies only; None → all.
        limit: Maximum number of results.
    """
    pat = f"%{query}%"

    where: list[str] = ["(name LIKE ? OR doc LIKE ?)"]
    params: list = [pat, pat]

    if pkg is not None:
        lit = pkg if pkg == "__project__" else normalize(pkg)
        where.append("pkg = ?")
        params.append(lit)

    if internal is True:
        where.append("pkg = '__project__'")
    elif internal is False:
        where.append("pkg != '__project__'")

    params.append(limit)
    sql = (
        "SELECT pkg, module, name, kind, signature, returns, params, doc"
        " FROM symbols"
        f" WHERE {' AND '.join(where)}"
        " LIMIT ?"
    )
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]
