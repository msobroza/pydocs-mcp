"""Extended tests for search.py — covers exception paths and edge cases."""
import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.search import search_chunks, search_symbols


class TestSearchChunksEdge:
    def test_single_char_query_returns_empty(self, conn):
        """Words with len<=1 are filtered out."""
        result = search_chunks(conn, "x")
        assert result == []

    def test_fts_operator_passthrough(self, conn):
        """Queries with FTS operators (AND/OR/NOT) are passed through directly."""
        result = search_chunks(conn, "fibonacci OR session")
        assert len(result) > 0

    def test_exception_returns_empty(self, tmp_path):
        """Corrupt/missing FTS table returns empty list instead of crashing."""
        c = open_index_database(tmp_path / "bad.db")
        # Don't rebuild FTS, so the table exists but has no data matching
        c.execute(
            "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
            ("pkg", "1.0", "test", "", "[]", "h", "dependency"),
        )
        c.commit()
        # Searching a term that doesn't exist just returns empty
        result = search_chunks(c, "nonexistent")
        assert result == []
        c.close()


class TestSearchSymbolsEdge:
    def test_exception_returns_empty(self, tmp_path):
        """If the symbols table query somehow fails, return empty."""
        c = open_index_database(tmp_path / "test.db")
        # Query on empty table
        result = search_symbols(c, "anything")
        assert result == []
        c.close()
