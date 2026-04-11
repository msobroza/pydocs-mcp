"""Search functions: FTS5 for docs, LIKE for symbols."""
from __future__ import annotations

import sqlite3

from pydocs_mcp.constants import CONTEXT_TOKEN_BUDGET
from pydocs_mcp.deps import normalize

# Approximate characters per token (conservative estimate for English text).
_CHARS_PER_TOKEN = 4


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
        internal: True \u2192 project source only; False \u2192 dependencies only; None \u2192 all.
        topic: If given, restricts results to chunks whose heading contains this string (LIKE).
        limit: Maximum number of results.
    """
    # If the query already uses FTS operators, pass it through directly.
    # Otherwise split into words, quote each for exact matching, and join with OR.
    _FTS_OPS = {"OR", "AND", "NOT"}
    tokens = query.split()
    if any(t in _FTS_OPS for t in tokens):
        fts_q = query
    else:
        words = [w for w in tokens if len(w) > 1]
        if not words:
            return []
        fts_q = " OR ".join(f'"{w}"' for w in words)

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


def concat_context(hits: list[dict], max_tokens: int = CONTEXT_TOKEN_BUDGET) -> str:
    """Concatenate chunk headings and bodies until the token budget is reached.

    Produces a single text blob from ranked search results, similar to how
    Neuledge Context and Context7 return documentation. The budget ensures
    the response fits within LLM context windows.

    Args:
        hits: Ordered list of search result dicts (best first), each with
              'heading' and 'body' keys.
        max_tokens: Maximum tokens to include (default: CONTEXT_TOKEN_BUDGET).

    Returns:
        Concatenated text within the token budget.
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for h in hits:
        heading = h.get("heading", "")
        body = h.get("body", "")
        chunk = f"## {heading}\n{body}\n"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(chunk[:remaining])
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)


def search_symbols(
    conn: sqlite3.Connection,
    query: str,
    pkg: str | None = None,
    limit: int = 15,
    internal: bool | None = None,
) -> list[dict]:
    """Symbol LIKE search on name and docstring.

    Args:
        query: Fragment to match against symbol name or docstring (case-insensitive LIKE).
        pkg: Restrict to a specific package name. '__project__' is matched literally.
        internal: True \u2192 project symbols only; False \u2192 dependency symbols only; None \u2192 all.
        limit: Maximum number of results.
    """
    # Escape LIKE special chars so user input is treated literally.
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = f"%{escaped}%"

    where: list[str] = ["(lower(name) LIKE ? ESCAPE '\\' OR lower(doc) LIKE ? ESCAPE '\\')"]
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
