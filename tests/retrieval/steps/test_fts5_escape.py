# TDD red phase: failing tests for _build_fts_match_query punctuation escape.
#
# Pre-fix the helper wraps each whitespace-split token in double-quoted
# FTS5 phrase notation but strips nothing first. Tokens that paste raw
# Python code into the query (triple-quoted strings, single-quoted module
# names, code-style commas inside brackets) crash SQLite with
# ``fts5: syntax error near ","``: the embedded quote breaks out of the
# phrase wrap and the trailing comma then sits unquoted at FTS5 top level.
# These tests pin the post-fix behaviour.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.steps.chunk_fetcher import _build_fts_match_query


# DS-1000 small_test tasks 14 + 23 — the two prompts that aborted the
# BM25 sweep pre-fix. Real-world regression baseline.
_DS1000_FAILING_PROMPTS = [
    # Task 14: triple-quoted Python with embedded commas inside a token.
    "Problem:\nI have the following data frame:\n"
    "import pandas as pd\nimport io\nfrom scipy import stats\n"
    'temp=u"""probegenes,sample1,sample2,sample3\n'
    '1415777_at Pnliprp1,20,0.00,11.00\n"""\n',
    # Task 23: single-quoted module name + the bare token "set" downstream.
    "Problem:\n\nI am trying to run an Elastic Net regression but get the "
    "following error: NameError: name 'sklearn' is not defined. The set of "
    "parameters I'm passing is shown below.\n",
]

# Synthetic regression cases — each token shape FTS5 rejected pre-fix.
_SYNTHETIC_PUNCT_QUERIES = [
    "df.iloc[:, 0]",                    # comma + brackets
    "np.array([1,2,3])",                # parens + commas
    "pd.DataFrame(*args, **kwargs)",    # stars + parens + commas
    'config["key"] = value',            # embedded double-quotes
    "obj.attr = 'literal'",             # embedded single-quotes
    "a:b c:d",                          # FTS5 column-qualifier syntax
    '""""',                             # all double-quotes — degenerate but real
]


@pytest.fixture
def fts5_probe(tmp_path: Path):
    # A throwaway FTS5 virtual table for round-tripping MATCH expressions.
    f = tmp_path / "fts5_probe.db"
    conn = sqlite3.connect(str(f))
    conn.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    conn.execute("INSERT INTO probe (text) VALUES ('dummy row so MATCH runs')")
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize("raw", _DS1000_FAILING_PROMPTS)
def test_ds1000_failing_prompts_accepted_by_fts5(raw, fts5_probe) -> None:
    # The two real DS-1000 prompts must produce a MATCH expression that
    # FTS5 accepts. Pre-fix this raised
    # ``sqlite3.OperationalError: fts5: syntax error near ","``.
    fts = _build_fts_match_query(raw)
    assert fts is not None
    list(fts5_probe.execute("SELECT * FROM probe WHERE probe MATCH ?", [fts]))


@pytest.mark.parametrize("raw", _SYNTHETIC_PUNCT_QUERIES)
def test_synthetic_punctuation_accepted_by_fts5(raw, fts5_probe) -> None:
    # Code-paste queries with FTS5-reserved punctuation either return
    # None (no usable word token survives stripping) or yield a MATCH
    # expression FTS5 accepts.
    fts = _build_fts_match_query(raw)
    if fts is None:
        return
    list(fts5_probe.execute("SELECT * FROM probe WHERE probe MATCH ?", [fts]))


def test_preserves_dotted_identifier() -> None:
    # ``pd.DataFrame`` stays one phrase so BM25 ranks the full dotted
    # name higher than two separate words.
    fts = _build_fts_match_query("pd.DataFrame is great")
    assert fts is not None
    assert '"pd.DataFrame"' in fts


def test_preserves_hyphenated_identifier() -> None:
    # ``multi-index`` stays one phrase — FTS5 unicode61 treats hyphen
    # as a word char in code contexts.
    fts = _build_fts_match_query("multi-index join")
    assert fts is not None
    assert '"multi-index"' in fts
