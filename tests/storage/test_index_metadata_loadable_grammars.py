"""``loadable_grammars`` round-trips through the index_metadata row mappers.

The stamp is the sorted CSV ``loadable_grammar_fingerprint()`` produced at
index time, narrowed to what the pass re-checked (``stamped_grammars``) — the
tree-sitter extensions whose reference graph this bundle can actually vouch
for. It is what lets ``get_references``' ``meta.resolution`` describe the INDEX
rather than the serving process (ADR 0022 follow-up; issue #246 item 3).
"""

import sqlite3

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import MULTILANG_EXTENSIONS
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.index_metadata import (
    IndexMetadata,
    PriorBundleState,
    format_grammar_stamp,
    parse_grammar_stamp,
    read_index_metadata,
    read_prior_bundle_state,
    write_index_metadata,
)

# Derived, not spelled out: an eighth language must not escape these pins
# (the chunker/analyzer drift guard covers neither this file nor the stamp).
_EVERY_GRAMMAR = format_grammar_stamp(MULTILANG_EXTENSIONS)


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


def test_grammar_loaded_never_matches_an_empty_extension() -> None:
    """``"".split(",")`` is ``[""]``, so a naive membership test made the
    EMPTY extension "loaded" on every unstamped bundle."""
    assert _meta("").grammar_loaded("") is False
    assert _meta(".rs").grammar_loaded("") is False


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
    the SELECT would raise "no such column" through such a connection. Served
    bundles are migrated on load, so this pins the documented contract rather
    than the common path. It reads back as the unstamped shape instead."""
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


# --- the stamp's CSV format has one parser and one formatter ----------------


def test_parse_grammar_stamp_drops_the_empty_extension() -> None:
    assert parse_grammar_stamp("") == frozenset()
    assert parse_grammar_stamp(".rs,.ts") == frozenset({".rs", ".ts"})


def test_format_grammar_stamp_sorts_and_round_trips() -> None:
    assert format_grammar_stamp({".ts", ".rs"}) == ".rs,.ts"
    assert format_grammar_stamp(()) == ""
    assert format_grammar_stamp(parse_grammar_stamp(_EVERY_GRAMMAR)) == _EVERY_GRAMMAR


# --- what a bundle held BEFORE a pass, per scope -----------------------------


def _bundle_with(db, *names: str) -> sqlite3.Connection:
    c = open_index_database(db)
    for name in names:
        c.execute("INSERT INTO packages (name, content_hash) VALUES (?, 'h')", (name,))
    c.commit()
    return c


def test_prior_state_of_a_fresh_bundle_is_empty(tmp_path) -> None:
    c = _bundle_with(tmp_path / "fresh.db")
    assert read_prior_bundle_state(c) == PriorBundleState.empty()
    assert PriorBundleState.empty() == PriorBundleState(
        "", has_project_package=False, has_dependency_packages=False
    )
    c.close()


def test_prior_state_tells_the_project_scope_from_the_dependency_scope(tmp_path) -> None:
    """The policy needs the two scopes apart: `--skip-deps` on a bundle that
    never indexed dependencies leaves nothing unchecked, while the same flag
    on one that did must not widen the stamp."""
    project_only = _bundle_with(tmp_path / "p.db", PROJECT_PACKAGE_NAME)
    assert read_prior_bundle_state(project_only) == PriorBundleState(
        "", has_project_package=True, has_dependency_packages=False
    )
    project_only.close()
    deps_only = _bundle_with(tmp_path / "d.db", "requests", "attrs")
    assert read_prior_bundle_state(deps_only) == PriorBundleState(
        "", has_project_package=False, has_dependency_packages=True
    )
    deps_only.close()


def test_prior_state_carries_the_previous_stamp(tmp_path) -> None:
    c = _bundle_with(tmp_path / "s.db", PROJECT_PACKAGE_NAME, "requests")
    write_index_metadata(c, _meta(".rs,.ts"))
    assert read_prior_bundle_state(c) == PriorBundleState(
        ".rs,.ts", has_project_package=True, has_dependency_packages=True
    )
    c.close()
