"""``loadable_grammars`` round-trips through the index_metadata row mappers.

The stamp is the sorted CSV ``loadable_grammar_fingerprint()`` produced at
index time — the tree-sitter extensions whose grammar loaded, i.e. the languages
whose reference graph this bundle can actually contain. It is what lets
``get_references``' ``meta.resolution`` describe the INDEX rather than the
serving process (ADR 0022 follow-up; issue #246 item 3).
"""

import sqlite3

import pytest

from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    read_index_metadata,
    write_index_metadata,
)

_EVERY_GRAMMAR = ".c,.h,.java,.js,.rs,.ts,.tsx"


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
        "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
        "indexed_at REAL, git_head TEXT, loadable_grammars TEXT)"
    )
    yield c
    c.close()


def _meta(loadable_grammars: str = "") -> IndexMetadata:
    return IndexMetadata(
        project_name="p",
        project_root="/p",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=1.0,
        loadable_grammars=loadable_grammars,
    )


def test_loadable_grammars_round_trips(conn) -> None:
    write_index_metadata(conn, _meta(_EVERY_GRAMMAR))
    got = read_index_metadata(conn)
    assert got is not None and got.loadable_grammars == _EVERY_GRAMMAR


def test_loadable_grammars_defaults_empty_and_null_reads_empty(conn) -> None:
    assert _meta().loadable_grammars == ""  # dataclass default keeps old ctors valid
    write_index_metadata(conn, _meta())
    conn.execute("UPDATE index_metadata SET loadable_grammars = NULL")
    got = read_index_metadata(conn)
    assert got is not None and got.loadable_grammars == ""


def test_legacy_fallback_has_no_loadable_grammars() -> None:
    legacy = IndexMetadata.legacy_fallback(project_name="p", embedding_model=None)
    assert legacy.loadable_grammars == ""


def test_grammar_loaded_reads_the_stamp() -> None:
    stamped = _meta(".rs,.ts")
    assert stamped.grammar_loaded(".rs") is True
    assert stamped.grammar_loaded(".ts") is True
    assert stamped.grammar_loaded(".js") is False


def test_an_unstamped_bundle_declines_every_grammar() -> None:
    """A bundle built before the stamp existed reads back ``""`` — the same
    value as one stamped while NO grammar loaded. Both mean the bundle cannot
    vouch for a code-language graph, and both decline (owner ruling: never
    claim what the index cannot show)."""
    assert _meta("").grammar_loaded(".rs") is False
    assert (
        IndexMetadata.legacy_fallback(project_name="p", embedding_model=None).grammar_loaded(".rs")
        is False
    )


def test_a_pre_v17_table_reads_back_unstamped_instead_of_raising(tmp_path) -> None:
    """``read_index_metadata`` is documented as callable on an UN-MIGRATED
    connection (the freshness probe uses a plain ``sqlite3.connect``). A bundle
    stamped at v11..v16 has no ``loadable_grammars`` column yet; naming it in
    the SELECT would raise "no such column" at the first poll of every
    existing bundle. It reads back as the unstamped shape instead."""
    c = sqlite3.connect(tmp_path / "old.db")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE index_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, embedding_provider TEXT, "
        "embedding_model TEXT, embedding_dim INTEGER, pipeline_hash TEXT, "
        "indexed_at REAL, git_head TEXT)"
    )
    c.execute(
        "INSERT INTO index_metadata (id, project_name, indexed_at, git_head) "
        "VALUES (1, 'p', 1.0, 'abc')"
    )
    got = read_index_metadata(c)
    assert got is not None
    assert got.git_head == "abc"
    assert got.loadable_grammars == ""
    assert got.grammar_loaded(".rs") is False
    c.close()
