"""v18 migration — the branch-keyed tree tier and the landing-unit columns (spec §6.1, P1).

v17 is what every 0.8.x user holds, so v17 → v18 is the primary path: the
bundle reopens as v18 with every row intact (same values, same rowids), the
project's tree-tier rows carry the default branch's name, dependency rows stay
``''``, and NOTHING forces a re-extraction or a re-embed. v16 takes the same
step; v12..v15 keep their project-hash clear and find no default branch to
stamp. ``tests/_schema_v17_bundle.py`` holds the frozen v17 DDL.
"""

from __future__ import annotations

import functools
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp import db as db_module
from pydocs_mcp import db_branch_key_migration as branch_key_migration
from pydocs_mcp.db import BRANCH_TABLES_SCHEMA_VERSION, SCHEMA_VERSION, open_index_database
from pydocs_mcp.storage.sqlite import document_tree_store as tree_store
from tests._schema_v17_bundle import (
    V17_DDL,
    create_v17_bundle,
    downgrade_bundle_to_v17,
    table_rows,
    table_shapes,
)

_PROJECT = "__project__"

# Rows of every tree-tier table, a dependency row FIRST so the rebuilt tables'
# rowid order interleaves the two tiers; ``pkg.gone`` is deleted below, leaving
# a rowid gap that only a rowid-preserving copy keeps.
_SEED_SQL = """
    INSERT INTO packages (name, content_hash, origin) VALUES ('__project__', 'h1', 'project');
    INSERT INTO packages (name, content_hash, origin) VALUES ('requests', 'h2', 'dependency');
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('__project__', 't', 'body', 'c1', 1);
    INSERT INTO chunks (package, title, text, content_hash, embedded)
        VALUES ('requests', 'r', 'dep body', 'c2', 0);
    INSERT INTO document_trees (package, module, tree_json, content_hash, updated_at)
        VALUES ('requests', 'requests.api', '{"d":1}', 'td', 1.0);
    INSERT INTO document_trees (package, module, tree_json, content_hash, updated_at)
        VALUES ('__project__', 'pkg.gone', '{"g":1}', 'tg', 1.0);
    INSERT INTO document_trees (package, module, tree_json, content_hash, updated_at)
        VALUES ('__project__', 'pkg.a', '{"p":1}', 'ta', 2.0);
    DELETE FROM document_trees WHERE module = 'pkg.gone';
    INSERT INTO node_references (from_package, from_node_id, to_name, to_node_id, kind)
        VALUES ('requests', 'requests.api.get', 'x', NULL, 'calls');
    INSERT INTO node_references (from_package, from_node_id, to_name, to_node_id, kind)
        VALUES ('__project__', 'pkg.a.f', 'g', 'pkg.b.g', 'calls');
    INSERT INTO node_scores (package, qualified_name, in_degree, pagerank, community)
        VALUES ('requests', 'requests.api.get', 1, 0.1, 0);
    INSERT INTO node_scores (package, qualified_name, in_degree, pagerank, community)
        VALUES ('__project__', 'pkg.a.f', 2, 0.5, 1);
    INSERT INTO module_members (package, module, name, kind)
        VALUES ('requests', 'requests.api', 'get', 'function');
    INSERT INTO module_members (package, module, name, kind)
        VALUES ('__project__', 'pkg.a', 'f', 'function');
    INSERT INTO decision_records (package, title, status, source, confidence, evidence,
        affected_files, affected_qnames, created_at, updated_at)
        VALUES ('__project__', 'd', 'accepted', 'adr_files', 1.0, '[]', '[]', '[]', 1.0, 1.0);
    INSERT INTO index_metadata (id, project_name, pipeline_hash, indexed_at, git_head,
        loadable_grammars) VALUES (1, 'proj', 'ph', 1.0, 'abc', '.rs');
    INSERT INTO branches (name, head_sha, source, is_default, pipeline_hash, indexed_at,
        last_used_at) VALUES ('main', 'aaaa', 'working_tree', 1, 'p', 1.0, 1.0);
    INSERT INTO branch_files (branch, path, blob_sha) VALUES ('main', 'pkg/a.py', 'b1');
    INSERT INTO branch_chunks (branch, chunk_id, source_path) VALUES ('main', 1, 'pkg/a.py');
"""

# The five tables v18 keys by branch, with the column naming their package.
_BRANCH_KEYED = (
    ("document_trees", "package"),
    ("module_members", "package"),
    ("node_references", "from_package"),
    ("node_scores", "package"),
    ("decision_records", "package"),
)
_LANDING_COLUMNS = (
    "landing_kind",
    "landed_at",
    "diff_generation_key",
    "merge_evidence",
    "landing_sha",
    "upstream_gone",
)


def _v17_db(path: Path) -> Path:
    create_v17_bundle(path, _SEED_SQL)
    return path


def _v16_db(path: Path) -> Path:
    """v17 minus ``index_metadata.loadable_grammars`` — the 0.7.x shape."""
    _v17_db(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("ALTER TABLE index_metadata DROP COLUMN loadable_grammars")
        conn.execute("PRAGMA user_version = 16")
        conn.commit()
    return path


def _v15_db(path: Path) -> Path:
    """v16 minus the branch dimension's tables — no default branch to stamp."""
    _v16_db(path)
    with closing(sqlite3.connect(path)) as conn:
        for table in ("branches", "branch_files", "file_extractions", "branch_chunks"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DROP INDEX ix_chunks_content_hash")
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
    return path


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _pk(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in sorted((r for r in rows if r[5] > 0), key=lambda r: r[5])]


def _branches_by_package(conn: sqlite3.Connection, table: str, package_column: str) -> set:
    return set(conn.execute(f"SELECT {package_column}, branch FROM {table}").fetchall())


def _snapshot_every_row(path: Path) -> dict[str, list[tuple]]:
    """Every table's rows over its CURRENT columns, rowid first."""
    with closing(sqlite3.connect(path)) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'chunks_fts%'"
            )
        ]
        return {t: table_rows(conn, t, ", ".join(_columns(conn, t))) for t in tables}


def _rows_over(path: Path, tables: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """The same tables as ``tables``, read from ``path`` over their v17 columns."""
    with closing(sqlite3.connect(path)) as conn:
        return {t: table_rows(conn, t, ", ".join(_v17_columns(t))) for t in tables}


@functools.cache
def _v17_columns(table: str) -> tuple[str, ...]:
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.executescript(V17_DDL)
        return tuple(_columns(scratch, table))
    finally:
        scratch.close()


def test_schema_version_is_18_and_the_branches_verb_gate_stays_16() -> None:
    assert SCHEMA_VERSION == 18
    assert BRANCH_TABLES_SCHEMA_VERSION == 16


def test_fresh_db_keys_the_tree_tier_by_branch(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "fresh.db")
    try:
        assert _pk(conn, "document_trees") == ["branch", "package", "module"]
        assert _pk(conn, "node_references") == [
            "branch",
            "from_package",
            "from_node_id",
            "to_name",
            "kind",
        ]
        assert _pk(conn, "node_scores") == ["branch", "package", "qualified_name"]
        assert "branch" in _columns(conn, "module_members")
        assert "branch" in _columns(conn, "decision_records")
        assert _columns(conn, "branches")[-len(_LANDING_COLUMNS) :] == list(_LANDING_COLUMNS)
        assert _columns(conn, "index_metadata")[-1] == "diff_retain_hash"
        assert _columns(conn, "landing_patch_ids") == ["sha", "patch_id"]
        assert "landing_patch_ids" in db_module._KNOWN_TABLES
    finally:
        conn.close()


def test_a_migrated_v17_bundle_has_exactly_the_fresh_v18_shape(tmp_path: Path) -> None:
    """The fresh DDL and the migration must land on one shape — same columns in
    the same order, same keys, same indexes — or a fresh bundle and an upgraded
    one would answer the same query through different plans."""
    open_index_database(tmp_path / "fresh.db").close()
    open_index_database(_v17_db(tmp_path / "old.db")).close()
    with (
        closing(sqlite3.connect(tmp_path / "fresh.db")) as fresh,
        closing(sqlite3.connect(tmp_path / "old.db")) as old,
    ):
        assert table_shapes(old) == table_shapes(fresh)


def test_v17_bundle_reads_back_as_v18_with_every_row_intact(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "old.db")
    before = _snapshot_every_row(db)
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18
    # Same values under the same rowids — including the gap ``pkg.gone`` left,
    # which only a rowid-preserving copy of the rebuilt tables keeps.
    assert _rows_over(db, before) == before


def test_v17_migration_stamps_project_rows_with_the_default_branch_only(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "old.db")
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        for table, package_column in _BRANCH_KEYED:
            stamped = _branches_by_package(conn, table, package_column)
            if table == "decision_records":
                assert stamped == {(_PROJECT, "main")}
            else:
                assert stamped == {(_PROJECT, "main"), ("requests", "")}, table


def test_v17_migration_clears_no_content_hash_and_re_embeds_nothing(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "old.db")
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        hashes = dict(conn.execute("SELECT name, content_hash FROM packages"))
        assert hashes == {_PROJECT: "h1", "requests": "h2"}
        chunks = conn.execute("SELECT content_hash, embedded FROM chunks ORDER BY id").fetchall()
        assert chunks == [("c1", 1), ("c2", 0)]


def test_v17_migration_keeps_every_index(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "old.db")
    with closing(sqlite3.connect(db)) as conn:
        v17_indexes = {k for k in table_shapes(conn) if k.startswith("index:")}
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        v18_indexes = {k for k in table_shapes(conn) if k.startswith("index:")}
    assert v18_indexes == v17_indexes | {
        "index:ix_branches_landing",
        "index:idx_trees_package_module",
    }


# The package listing as v17 ran it, before the tree tier had a branch key.
_V17_LOAD_PACKAGE_TREES_SQL = (
    "SELECT module, tree_json FROM document_trees WHERE package=? ORDER BY rowid"
)


def _query_plan(conn: sqlite3.Connection, sql: str, params: tuple[str | None, ...]) -> str:
    return " | ".join(row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params))


@pytest.mark.parametrize("migrated", [False, True], ids=["fresh", "migrated"])
def test_tree_point_lookups_seek_package_and_module(tmp_path: Path, migrated: bool) -> None:
    """``load`` / ``exists`` seek on (package, module): their branch clause
    (#307) is a filter the unary ``+`` keeps out of the index choice, and the
    branch-led key alone cannot serve the probe. Without an index of their own
    they scan the package's whole range (~14 ms a probe on an 8,000-module
    package, against 0.01 ms on v17) — and ``LookupService`` probes several
    candidates per tool call."""
    db = _v17_db(tmp_path / "b.db") if migrated else tmp_path / "b.db"
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        for sql in (tree_store._LOAD_TREE_SQL, tree_store._TREE_EXISTS_SQL):
            plan = _query_plan(conn, sql, (_PROJECT, "pkg.a", None))
            assert "idx_trees_package_module (package=? AND module=?)" in plan, plan


def test_a_package_tree_listing_keeps_the_v17_plan_and_rowid_order(tmp_path: Path) -> None:
    """The (package, module) index must not steal the package listing: v17
    served it through ``idx_trees_package`` in rowid order, and the listing is a
    dict whose order reaches the answers. The branch clause (#307) adds only
    its default-branch subquery; the table access stays v17's."""
    db = _v17_db(tmp_path / "old.db")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO document_trees (package, module, tree_json) "
            "VALUES ('__project__', 'pkg.0', '{}')"
        )
        conn.commit()
        v17_plan = _query_plan(conn, _V17_LOAD_PACKAGE_TREES_SQL, (_PROJECT,))
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        v18_plan = _query_plan(conn, tree_store._LOAD_PACKAGE_TREES_SQL, (_PROJECT, None))
        listed = conn.execute(tree_store._LOAD_PACKAGE_TREES_SQL, (_PROJECT, None)).fetchall()
    assert v18_plan.split(" | ")[0] == v17_plan
    assert [module for module, _ in listed] == ["pkg.a", "pkg.0"]


def test_v17_bundle_without_a_default_branch_leaves_rows_unstamped(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "old.db")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DELETE FROM branches")
        conn.commit()
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        for table, package_column in _BRANCH_KEYED:
            assert {b for _, b in _branches_by_package(conn, table, package_column)} == {""}


def test_v16_bundle_takes_the_same_step_without_a_hash_clear(tmp_path: Path) -> None:
    db = _v16_db(tmp_path / "v16.db")
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "loadable_grammars" in _columns(conn, "index_metadata")
        assert dict(conn.execute("SELECT name, content_hash FROM packages")) == {
            _PROJECT: "h1",
            "requests": "h2",
        }
        assert _branches_by_package(conn, "node_references", "from_package") == {
            (_PROJECT, "main"),
            ("requests", ""),
        }


def test_v15_bundle_clears_the_project_hash_and_has_no_branch_to_stamp(tmp_path: Path) -> None:
    db = _v15_db(tmp_path / "v15.db")
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert dict(conn.execute("SELECT name, content_hash FROM packages")) == {
            _PROJECT: None,
            "requests": "h2",
        }
        for table, package_column in _BRANCH_KEYED:
            assert {b for _, b in _branches_by_package(conn, table, package_column)} == {""}


def test_reopening_a_migrated_bundle_is_idempotent(tmp_path: Path) -> None:
    db = _v17_db(tmp_path / "twice.db")
    open_index_database(db).close()
    once = _snapshot_every_row(db)
    open_index_database(db).close()
    assert _snapshot_every_row(db) == once
    with closing(sqlite3.connect(db)) as conn:
        leftovers = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%pre_v18%'"
        ).fetchall()
    assert leftovers == []


def test_a_v18_bundle_missing_the_branch_key_is_repaired_without_a_stamp(tmp_path: Path) -> None:
    """Drift repair rebuilds the key; stamping is the version step's job, so the
    repaired project rows stay ``''`` rather than guessing a branch."""
    db = _v17_db(tmp_path / "drift.db")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA user_version = 18")
        conn.commit()
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        assert _pk(conn, "document_trees") == ["branch", "package", "module"]
        assert {b for _, b in _branches_by_package(conn, "document_trees", "package")} == {""}


def test_a_missing_tree_tier_table_is_created_in_the_v18_shape(tmp_path: Path) -> None:
    """The v9..v11 arm replays only the sweeps newer than v9, so a drifted
    bundle can reach the v18 step without ``document_trees``; the step creates
    it keyed by branch instead of failing — a failed open wipes the bundle."""
    db = _v17_db(tmp_path / "drift.db")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP TABLE document_trees")
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        assert _pk(conn, "document_trees") == ["branch", "package", "module"]
        assert conn.execute("SELECT COUNT(*) FROM node_references").fetchone()[0] == 2


def test_a_failed_key_rebuild_rolls_back_whole(tmp_path: Path, monkeypatch) -> None:
    """The v18 sweep is one transaction: SQLite runs DDL outside a transaction
    in autocommit, so without one a crash or a kill after the first rename would
    strand the old rows in a side table no later open reads — the project's
    trees lost, with no hash cleared to re-extract them. This drives the
    transaction boundary directly: a rollback after a mid-sweep error restores
    the v17 shape whole, which is also what SQLite's journal does after a crash.
    (``open_index_database`` then re-raises an environmental error and rebuilds
    on a schema error — tests/test_db_migration_lock.py.)"""
    db = _v17_db(tmp_path / "old.db")
    before = _snapshot_every_row(db)
    first, second, third = branch_key_migration.TREE_TIER_KEY_REBUILDS
    broken = replace(second, ddl="CREATE TABLE node_references (not valid sql")
    monkeypatch.setattr(branch_key_migration, "TREE_TIER_KEY_REBUILDS", (first, broken, third))
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            db_module._apply_v18_additions(conn)
        conn.rollback()
    finally:
        conn.close()
    # Whole: the column adds and the first (successful) rebuild are undone too,
    # and no side table is left behind.
    assert _snapshot_every_row(db) == before
    with closing(sqlite3.connect(db)) as check:
        assert _pk(check, "document_trees") == ["package", "module"]


def test_the_v17_test_bundle_matches_what_a_downgrade_produces(tmp_path: Path) -> None:
    """Fidelity of the shared helper: ``downgrade_bundle_to_v17`` must land on
    the frozen ``V17_DDL`` shape exactly, or the byte-identity suite would be
    comparing against a bundle no 0.8.x process could have written."""
    create_v17_bundle(tmp_path / "frozen.db")
    open_index_database(tmp_path / "current.db").close()
    downgrade_bundle_to_v17(tmp_path / "current.db")
    with (
        closing(sqlite3.connect(tmp_path / "frozen.db")) as a,
        closing(sqlite3.connect(tmp_path / "current.db")) as b,
    ):
        assert table_shapes(b) == table_shapes(a)
        assert b.execute("PRAGMA user_version").fetchone()[0] == 17
