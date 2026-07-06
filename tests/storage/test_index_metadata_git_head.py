"""git_head round-trips through the index_metadata row mappers (spec §D4)."""

import sqlite3

import pytest

from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
        "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
        "indexed_at REAL, git_head TEXT)"
    )
    yield c
    c.close()


def _meta(git_head: str = "") -> IndexMetadata:
    return IndexMetadata(
        project_name="p",
        project_root="/p",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=1.0,
        git_head=git_head,
    )


def test_git_head_round_trips(conn) -> None:
    write_index_metadata(conn, _meta(git_head="a" * 40))
    got = read_index_metadata(conn)
    assert got is not None and got.git_head == "a" * 40


def test_git_head_defaults_empty_and_null_reads_empty(conn) -> None:
    assert _meta().git_head == ""  # dataclass default keeps old ctors valid
    write_index_metadata(conn, _meta())
    conn.execute("UPDATE index_metadata SET git_head = NULL")
    got = read_index_metadata(conn)
    assert got is not None and got.git_head == ""


def test_legacy_fallback_has_empty_git_head() -> None:
    legacy = IndexMetadata.legacy_fallback(project_name="p", embedding_model=None)
    assert legacy.git_head == ""
