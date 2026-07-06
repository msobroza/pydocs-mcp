"""Tests for ``build_fts_match_query`` — the FTS5 MATCH expression builder.

Regression guard for the crash where a stray operator word (an English
"OR"/"AND"/"NOT") inside natural-language or code text reached FTS5 ``MATCH``
raw and the parser interpreted punctuation like ``Problem:`` as a column name,
raising ``sqlite3.OperationalError: no such column: Problem``. The operator
token must match the (case-sensitive) ``_FTS_OPS`` vocabulary to take the
raw path, so the trigger is an uppercase operator word, e.g. ``ints OR``.

Single implementation: :func:`pydocs_mcp.storage.fts_query.build_fts_match_query`.
``storage.sqlite`` and ``retrieval.steps.chunk_fetcher`` both import it, so
byte-parity between the storage and fetcher paths holds by construction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.storage.fts_query import build_fts_match_query
from pydocs_mcp.storage.sqlite import SqliteChunkRepository, SqliteVectorStore


def test_single_source_of_truth() -> None:
    """storage.sqlite consumes the fts_query implementation — no local copy."""
    from pydocs_mcp.storage import sqlite as storage_sqlite

    assert storage_sqlite._build_fts_match_query is build_fts_match_query


def test_clean_operator_expression_passes_through() -> None:
    """A deliberate FTS expression (operator + all bare words) is untouched."""
    assert build_fts_match_query("foo OR bar") == "foo OR bar"


def test_operator_at_edge_passes_through() -> None:
    assert build_fts_match_query("x OR") == "x OR"


def test_stray_operator_word_in_natural_language_is_quoted() -> None:
    """A stray English ``OR`` inside punctuated text must NOT hijack the raw
    path — every token becomes a literal double-quoted term, so the resulting
    expression can never be parsed as ``no such column: Problem``."""
    raw = "Problem: I have ints OR arrays"
    result = build_fts_match_query(raw)

    assert result is not None
    assert result != raw
    assert '"Problem:"' in result
    assert '"OR"' in result


def test_operator_with_dotted_token_is_quoted() -> None:
    """An operator alongside a non-bare token (dots) falls through to quoting."""
    result = build_fts_match_query("alpha AND beta.gamma")
    assert result is not None
    assert result != "alpha AND beta.gamma"
    assert '"beta.gamma"' in result


def test_embedded_double_quote_is_escaped() -> None:
    """A token carrying an embedded double-quote (e.g. ``"shift"``) must have
    that quote DOUBLED per FTS5 string-literal escaping. The naive ``"<w>"``
    wrap would emit ``""shift""`` (empty phrase + bareword), unbalancing the
    quoting so later punctuation (``[``, ``:`` …) becomes a syntax error."""
    result = build_fts_match_query('is there a "shift" function')
    assert result is not None
    assert '"""shift"""' in result


def test_short_single_token_returns_none() -> None:
    assert build_fts_match_query("a") is None


def test_empty_terms_return_none() -> None:
    assert build_fts_match_query("") is None


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
                text="I have a list of ints or arrays to process",
                metadata={
                    ChunkFilterField.PACKAGE.value: "demo",
                    ChunkFilterField.TITLE.value: "Problem",
                    ChunkFilterField.MODULE.value: "demo.m",
                },
            ),
        ]
    )
    await repo.rebuild_index()
    return db_path


async def test_text_search_does_not_crash_on_stray_operator(populated_db: Path) -> None:
    """End-to-end regression guard: a query containing a punctuated phrase with
    a stray operator word must run against FTS5 ``MATCH`` WITHOUT raising
    ``sqlite3.OperationalError`` (the reported crash)."""
    provider = build_connection_provider(populated_db)
    store = SqliteVectorStore(provider=provider)
    # Must not raise sqlite3.OperationalError: no such column: Problem
    results = await store.text_search("Problem: foo OR bar", limit=10)
    assert isinstance(results, tuple)


async def test_text_search_does_not_crash_on_quotes_and_brackets(populated_db: Path) -> None:
    """End-to-end regression guard: a query carrying embedded quotes AND
    brackets (as a DS-1000 full prompt does, with code like ``x = [1, 2, 3]``)
    must run against FTS5 ``MATCH`` WITHOUT raising. The embedded-quote
    escaping keeps the quoting balanced so ``[`` stays a literal term."""
    provider = build_connection_provider(populated_db)
    store = SqliteVectorStore(provider=provider)
    # Previously raised sqlite3.OperationalError: fts5: syntax error near "["
    results = await store.text_search('how to "shift" an array x = [1, 2, 3]', limit=10)
    assert isinstance(results, tuple)
