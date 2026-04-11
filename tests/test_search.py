"""Tests for search functions."""
import sqlite3
import pytest
from pydocs_mcp.db import open_db
from pydocs_mcp.search import search_chunks, search_symbols


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "test.db")
    # Insert a package
    c.execute(
        "INSERT INTO packages(name,version,summary,homepage,requires) VALUES(?,?,?,?,?)",
        ("mypkg", "1.0", "test", "", "[]"),
    )
    # Insert a symbol whose name contains a literal underscore
    c.execute(
        "INSERT INTO symbols(pkg,module,kind,name,signature,doc,params,returns) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("mypkg", "mypkg.mod", "def", "get_value", "(x)", "Get value.", "[]", "int"),
    )
    # Insert another symbol that should NOT match percent queries
    c.execute(
        "INSERT INTO symbols(pkg,module,kind,name,signature,doc,params,returns) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("mypkg", "mypkg.mod", "def", "unrelated", "()", "Unrelated function.", "[]", "None"),
    )
    c.commit()
    return c


def test_search_symbols_percent_is_literal(conn):
    """A query of '100%' should find nothing, not return every symbol."""
    hits = search_symbols(conn, "100%")
    names = [h["name"] for h in hits]
    assert "unrelated" not in names, "% should not be treated as SQL wildcard"


def test_search_symbols_underscore_is_literal(conn):
    """A query of '_' should not match everything via SQL _ wildcard."""
    hits = search_symbols(conn, "_")
    # Should not return 'unrelated' (which has no underscore in name or doc)
    names = [h["name"] for h in hits]
    assert "unrelated" not in names, "_ should not be treated as SQL wildcard"


def test_search_symbols_finds_literal_underscore(conn):
    """A query of 'get_value' should find the symbol with that exact name."""
    hits = search_symbols(conn, "get_value")
    names = [h["name"] for h in hits]
    assert "get_value" in names
