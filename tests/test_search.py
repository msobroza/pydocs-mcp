"""Tests for internal/topic parameters on retrieve_chunks and retrieve_module_members."""
import sqlite3
import pytest
from pydocs_mcp.db import open_index_database
from pydocs_mcp.search import retrieve_chunks, retrieve_module_members


class TestSearchChunksInternal:
    def test_internal_true_returns_only_project(self, conn):
        results = retrieve_chunks(conn, "fibonacci", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)
        assert len(results) > 0

    def test_internal_true_excludes_deps(self, conn):
        # "get" only appears in requests dep chunks, so project-scoped search returns nothing
        results = retrieve_chunks(conn, "get", internal=True)
        assert results == []

    def test_internal_false_returns_only_deps(self, conn):
        results = retrieve_chunks(conn, "request", internal=False)
        assert all(r["pkg"] != "__project__" for r in results)
        assert len(results) > 0

    def test_internal_false_excludes_project(self, conn):
        # "fibonacci" only appears in __project__ chunks
        results = retrieve_chunks(conn, "fibonacci", internal=False)
        assert results == []

    def test_internal_none_returns_all(self, conn):
        results = retrieve_chunks(conn, "session OR fibonacci", internal=None)
        pkgs = {r["pkg"] for r in results}
        # Both packages must appear — none= means no scope filter
        assert "__project__" in pkgs and "sqlalchemy" in pkgs

    def test_internal_default_unchanged(self, conn):
        """Calling without internal= must preserve existing all-packages behaviour."""
        without = retrieve_chunks(conn, "session OR fibonacci")
        with_none = retrieve_chunks(conn, "session OR fibonacci", internal=None)
        assert without == with_none

    def test_internal_false_with_pkg_filter_combines(self, conn):
        results = retrieve_chunks(conn, "session", pkg="sqlalchemy", internal=False)
        assert all(r["pkg"] == "sqlalchemy" for r in results)
        assert len(results) > 0

    def test_internal_true_with_pkg_filter_returns_empty(self, conn):
        # pkg='sqlalchemy' AND internal=True is contradictory — should return nothing
        results = retrieve_chunks(conn, "session", pkg="sqlalchemy", internal=True)
        assert results == []


class TestSearchChunksTopic:
    def test_topic_filters_by_heading(self, conn):
        results = retrieve_chunks(conn, "fibonacci", topic="fibonacci")
        assert len(results) > 0, "Expected at least one chunk with 'fibonacci' in heading"
        assert all("fibonacci" in r["heading"].lower() for r in results)

    def test_topic_empty_string_means_no_filter(self, conn):
        with_empty = retrieve_chunks(conn, "fibonacci", topic="")
        without = retrieve_chunks(conn, "fibonacci")
        assert with_empty == without

    def test_topic_no_match_returns_empty(self, conn):
        results = retrieve_chunks(conn, "fibonacci", topic="nonexistent_heading_xyz")
        assert results == []


class TestSearchSymbolsInternal:
    def test_internal_true_returns_only_project(self, conn):
        results = retrieve_module_members(conn, "fibonacci", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)
        assert len(results) > 0

    def test_internal_true_excludes_deps(self, conn):
        # "get" only appears in dep symbols
        results = retrieve_module_members(conn, "get", internal=True)
        assert results == []

    def test_internal_false_returns_only_deps(self, conn):
        results = retrieve_module_members(conn, "get", internal=False)
        assert all(r["pkg"] != "__project__" for r in results)
        assert len(results) > 0

    def test_internal_false_excludes_project(self, conn):
        results = retrieve_module_members(conn, "fibonacci", internal=False)
        assert results == []

    def test_internal_none_returns_all(self, conn):
        # Both project and dep symbols should be found
        proj = retrieve_module_members(conn, "fibonacci", internal=None)
        dep = retrieve_module_members(conn, "get", internal=None)
        assert len(proj) > 0 and len(dep) > 0

    def test_internal_default_unchanged(self, conn):
        without = retrieve_module_members(conn, "get")
        with_none = retrieve_module_members(conn, "get", internal=None)
        assert without == with_none

    def test_internal_false_with_pkg_filter(self, conn):
        results = retrieve_module_members(conn, "session", pkg="sqlalchemy", internal=False)
        assert all(r["pkg"] == "sqlalchemy" for r in results)
        assert len(results) > 0

    def test_internal_true_with_pkg_filter_returns_empty(self, conn):
        # Contradictory: pkg='sqlalchemy' AND internal=True
        results = retrieve_module_members(conn, "session", pkg="sqlalchemy", internal=True)
        assert results == []


class TestSearchSymbolsLikeEscaping:
    """Tests from main: verify that LIKE special chars are escaped properly."""

    @pytest.fixture
    def like_conn(self, tmp_path):
        c = open_index_database(tmp_path / "like_test.db")
        c.execute(
            "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
            ("mypkg", "1.0", "test", "", "[]", "h", "dependency"),
        )
        c.execute(
            "INSERT INTO module_members(package,module,kind,name,signature,docstring,parameters,return_annotation) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("mypkg", "mypkg.mod", "function", "get_value", "(x)", "Get value.", "[]", "int"),
        )
        c.execute(
            "INSERT INTO module_members(package,module,kind,name,signature,docstring,parameters,return_annotation) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("mypkg", "mypkg.mod", "function", "unrelated", "()", "Unrelated function.", "[]", "None"),
        )
        c.commit()
        return c

    def test_retrieve_module_members_percent_is_literal(self, like_conn):
        """A query of '100%' should find nothing, not return every symbol."""
        hits = retrieve_module_members(like_conn, "100%")
        names = [h["name"] for h in hits]
        assert "unrelated" not in names, "% should not be treated as SQL wildcard"

    def test_retrieve_module_members_underscore_is_literal(self, like_conn):
        """A query of '_' should not match everything via SQL _ wildcard."""
        hits = retrieve_module_members(like_conn, "_")
        # Should not return 'unrelated' (which has no underscore in name or doc)
        names = [h["name"] for h in hits]
        assert "unrelated" not in names, "_ should not be treated as SQL wildcard"

    def test_retrieve_module_members_finds_literal_underscore(self, like_conn):
        """A query of 'get_value' should find the symbol with that exact name."""
        hits = retrieve_module_members(like_conn, "get_value")
        names = [h["name"] for h in hits]
        assert "get_value" in names
