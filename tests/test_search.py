"""Tests for internal/topic parameters on search_chunks and search_symbols."""
import pytest
from pydocs_mcp.search import search_chunks, search_symbols


class TestSearchChunksInternal:
    def test_internal_true_returns_only_project(self, conn):
        results = search_chunks(conn, "fibonacci", internal=True)
        assert all(r["pkg"] == "__project__" for r in results)
        assert len(results) > 0

    def test_internal_true_excludes_deps(self, conn):
        # "get" only appears in requests dep chunks, so project-scoped search returns nothing
        results = search_chunks(conn, "get", internal=True)
        assert results == []

    def test_internal_false_returns_only_deps(self, conn):
        results = search_chunks(conn, "request", internal=False)
        assert all(r["pkg"] != "__project__" for r in results)
        assert len(results) > 0

    def test_internal_false_excludes_project(self, conn):
        # "fibonacci" only appears in __project__ chunks
        results = search_chunks(conn, "fibonacci", internal=False)
        assert results == []

    def test_internal_none_returns_all(self, conn):
        results = search_chunks(conn, "session OR fibonacci", internal=None)
        pkgs = {r["pkg"] for r in results}
        # Both packages must appear — none= means no scope filter
        assert "__project__" in pkgs and "sqlalchemy" in pkgs

    def test_internal_default_unchanged(self, conn):
        """Calling without internal= must preserve existing all-packages behaviour."""
        without = search_chunks(conn, "session OR fibonacci")
        with_none = search_chunks(conn, "session OR fibonacci", internal=None)
        assert without == with_none

    def test_internal_false_with_pkg_filter_combines(self, conn):
        results = search_chunks(conn, "session", pkg="sqlalchemy", internal=False)
        assert all(r["pkg"] == "sqlalchemy" for r in results)
        assert len(results) > 0

    def test_internal_true_with_pkg_filter_returns_empty(self, conn):
        # pkg='sqlalchemy' AND internal=True is contradictory — should return nothing
        results = search_chunks(conn, "session", pkg="sqlalchemy", internal=True)
        assert results == []


class TestSearchChunksTopic:
    def test_topic_filters_by_heading(self, conn):
        results = search_chunks(conn, "fibonacci", topic="fibonacci")
        assert all("fibonacci" in r["heading"].lower() for r in results)

    def test_topic_empty_string_means_no_filter(self, conn):
        with_empty = search_chunks(conn, "fibonacci", topic="")
        without = search_chunks(conn, "fibonacci")
        assert with_empty == without

    def test_topic_no_match_returns_empty(self, conn):
        results = search_chunks(conn, "fibonacci", topic="nonexistent_heading_xyz")
        assert results == []
