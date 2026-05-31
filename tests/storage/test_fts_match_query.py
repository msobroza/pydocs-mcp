"""Tests for ``_build_fts_match_query`` — the FTS5 MATCH expression builder.

Regression guard for the crash where a stray operator word (an English
"OR"/"AND"/"NOT") inside natural-language or code text reached FTS5 ``MATCH``
raw and the parser interpreted punctuation like ``Problem:`` as a column name,
raising ``sqlite3.OperationalError: no such column: Problem``. The operator
token must match the (case-sensitive) ``_FTS_OPS`` vocabulary to take the
raw path, so the trigger is an uppercase operator word, e.g. ``ints OR``.

Two mirrored implementations must stay byte-identical:
- :func:`pydocs_mcp.storage.sqlite._build_fts_match_query`
- :func:`pydocs_mcp.retrieval.steps.chunk_fetcher._build_fts_match_query`
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.retrieval.steps.chunk_fetcher import (
    _build_fts_match_query as fetcher_build,
)
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteVectorStore,
    _build_fts_match_query as storage_build,
)


def test_clean_operator_expression_passes_through() -> None:
    """A deliberate FTS expression (operator + all bare words) is untouched."""
    assert storage_build("foo OR bar") == "foo OR bar"


def test_stray_operator_word_in_natural_language_is_quoted() -> None:
    """A stray English ``OR`` inside punctuated text must NOT hijack the raw
    path — every token becomes a literal double-quoted term, so the resulting
    expression can never be parsed as ``no such column: Problem``."""
    result = storage_build("Problem: I have ints OR arrays")
    raw = "Problem: I have ints OR arrays"

    # NOT raw passthrough (the bug): the punctuated input is reshaped.
    assert result != raw
    # Every surviving token is double-quoted; ``Problem:`` is one literal term.
    assert '"Problem:"' in result
    # The ``OR`` operator word became a literal quoted term, not an operator.
    assert '"OR"' in result


def test_embedded_double_quote_is_escaped() -> None:
    """A token carrying an embedded double-quote (e.g. ``"shift"``) must have
    that quote DOUBLED per FTS5 string-literal escaping. The naive ``"<w>"``
    wrap would emit ``""shift""`` (empty phrase + bareword), unbalancing the
    quoting so later punctuation (``[``, ``:`` …) becomes a syntax error."""
    result = storage_build('is there a "shift" function')
    # Embedded quotes doubled: the token ``"shift"`` -> ``"""shift"""``.
    assert '"""shift"""' in result


# Inputs that exercise both the raw-passthrough path and the quote-each-word
# fallback, including operator-word + punctuation cases that previously crashed.
_PARITY_INPUTS = (
    "foo OR bar",  # clean operator expr → passthrough
    "Problem: I have ints OR arrays",  # stray operator + punctuation → quoted
    "alpha AND beta",  # all bare words + operator → passthrough
    "alpha AND beta.gamma",  # operator + dotted token → quoted (not safe)
    "find NEAR(x)",  # operator + parens → quoted (not safe)
    "single",  # no operator → quoted
    "a",  # single short token → None
    "",  # empty → None
    "x OR",  # operator at edge, all bare → passthrough
    'is there a "shift" function',  # embedded quotes → doubled/escaped
    "x = np.array([1, 2, 3])",  # brackets/punctuation → quoted, no crash
)


@pytest.mark.parametrize("terms", _PARITY_INPUTS)
def test_storage_and_fetcher_impls_are_byte_identical(terms: str) -> None:
    """The two mirrored ``_build_fts_match_query`` impls must return the same
    string (or both ``None``) for every input — byte-parity invariant."""
    assert storage_build(terms) == fetcher_build(terms)


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
