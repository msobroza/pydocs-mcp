"""Regression tests: FTS5 operator tokens in invalid positions must not crash.

``build_fts_match_query`` passes a query through raw when an operator is
present and every token is a bare word — but FTS5's grammar is infix-only:
a leading / trailing / lone / doubled operator is a syntax error, so the raw
passthrough turned plausible natural-language queries ("AND gate
implementation", "NOT x") into ``sqlite3.OperationalError`` crashes inside
``text_search``. Verified against FTS5 directly: leading AND/NOT, trailing
OR, lone OR, and adjacent operators all raise; valid infix expressions and a
bare ``NEAR`` word do not.

The raw path must therefore only fire for positionally valid expressions;
everything else falls back to the quote-each-word branch, which is always
FTS5-safe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.storage.fts_query import build_fts_match_query
from pydocs_mcp.storage.sqlite import SqliteChunkRepository, SqliteLexicalStore

# Every entry raises "fts5: syntax error" when passed raw to MATCH.
_INVALID_OPERATOR_QUERIES = (
    "AND gate implementation",  # leading operator
    "NOT x",  # leading NOT (binary in FTS5, no unary form)
    "x OR",  # trailing operator
    "OR",  # lone operator
    "a AND OR b",  # adjacent operators
)

# Positionally valid deliberate expressions — the raw path must keep working.
_VALID_OPERATOR_QUERIES = (
    "foo OR bar",
    "a NOT b",
    "x AND y AND z",
    "machine learning OR deep",  # implicit AND between adjacent words is valid
)


@pytest.mark.parametrize("query", _INVALID_OPERATOR_QUERIES)
def test_invalid_operator_positions_fall_back_to_quoting(query: str) -> None:
    """An operator in a syntactically invalid position must NOT take the raw
    path — the builder returns a quoted form (or ``None``), never the raw
    string that FTS5 would reject."""
    assert build_fts_match_query(query) != query


@pytest.mark.parametrize("query", _VALID_OPERATOR_QUERIES)
def test_valid_operator_expressions_still_pass_through(query: str) -> None:
    assert build_fts_match_query(query) == query


def test_bare_near_word_is_not_treated_as_deliberate_expression() -> None:
    """A bare ``NEAR`` token is valid FTS5 (it parses as a plain term), but it
    is not evidence of a deliberate expression: without AND/OR/NOT the query
    takes the quoted branch like any other words."""
    result = build_fts_match_query("near miss")
    assert result == '"near" OR "miss"'


@pytest.fixture
async def populated_db(tmp_path: Path) -> Path:
    """A small SQLite with ``chunks_fts`` populated so FTS5 MATCH executes."""
    db_path = tmp_path / "fts.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert(
        [
            Chunk(
                text="the AND gate implementation uses x or y arrays",
                metadata={
                    ChunkFilterField.PACKAGE.value: "demo",
                    ChunkFilterField.TITLE.value: "gates",
                    ChunkFilterField.MODULE.value: "demo.m",
                },
            ),
        ]
    )
    await repo.rebuild_index()
    return db_path


@pytest.mark.parametrize("query", _INVALID_OPERATOR_QUERIES)
async def test_text_search_survives_invalid_operator_positions(
    populated_db: Path, query: str
) -> None:
    """End-to-end: the exact queries that crash FTS5 when passed raw must run
    through ``text_search`` without raising ``sqlite3.OperationalError``."""
    provider = build_connection_provider(populated_db)
    store = SqliteLexicalStore(provider=provider)
    results = await store.text_search(query, limit=10)
    assert isinstance(results, tuple)
