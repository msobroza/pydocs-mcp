"""A schema-v17 bundle for the v18 migration tests — what every 0.8.x user holds.

``V17_DDL`` is the fresh-database DDL of schema v17, frozen from ``db.py`` as of
0.8.1 (the last release before v18; SQL comments dropped, every column, default,
key and index kept). ``downgrade_bundle_to_v17`` rewrites a bundle the CURRENT
code indexed into exactly that shape — same rows, same rowids — so a test can
index a real project once, turn the bundle into what a 0.8.x process would have
left on disk, and reopen it through the v17 → v18 migration. ``table_shapes`` is
the fidelity check: a downgraded bundle and a fresh ``V17_DDL`` database report
identical shapes (``tests/test_db_schema_v18_migration.py`` pins it).

Why a downgrade and not an old checkout: the suite cannot run the 0.8.1 code,
and schema v18 leaves every writer's row values and every reader's SQL as they
were — only the three rebuilt keys, the added ``branch`` / landing /
``diff_retain_hash`` columns and one table differ, and those are exactly what
the downgrade removes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

V17_SCHEMA_VERSION = 17

V17_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT DEFAULT '',
        title TEXT, text TEXT, origin TEXT, content_hash TEXT, qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0, decision_id INTEGER,
        source_path TEXT, start_line INTEGER, end_line INTEGER
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package, content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE TABLE document_trees (
        package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL,
        content_hash TEXT, updated_at REAL,
        PRIMARY KEY (package, module)
    );
    CREATE TABLE node_references (
        from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, to_name TEXT NOT NULL,
        to_node_id TEXT, kind TEXT NOT NULL,
        PRIMARY KEY (from_package, from_node_id, to_name, kind)
    );
    CREATE TABLE chunk_multi_vector_ids (
        chunk_id INTEGER PRIMARY KEY, plaid_doc_id INTEGER NOT NULL UNIQUE,
        package TEXT NOT NULL, pipeline_hash TEXT NOT NULL,
        FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
    );
    CREATE TABLE node_scores (
        package TEXT NOT NULL, qualified_name TEXT NOT NULL,
        in_degree INTEGER NOT NULL DEFAULT 0, pagerank REAL NOT NULL DEFAULT 0.0,
        community INTEGER NOT NULL DEFAULT -1,
        PRIMARY KEY (package, qualified_name)
    );
    CREATE TABLE decision_records (
        id INTEGER PRIMARY KEY, package TEXT NOT NULL, title TEXT NOT NULL,
        status TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL,
        evidence TEXT NOT NULL, affected_files TEXT NOT NULL,
        affected_qnames TEXT NOT NULL, staleness_score REAL NOT NULL DEFAULT 0.0,
        superseded_by INTEGER, verification TEXT NOT NULL DEFAULT 'verbatim',
        structured TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_chunks_module          ON chunks(module);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
    CREATE INDEX ix_refs_from              ON node_references(from_package, from_node_id);
    CREATE INDEX ix_refs_to_name           ON node_references(to_name);
    CREATE INDEX ix_refs_to_node           ON node_references(to_node_id);
    CREATE INDEX idx_cmv_plaid_doc_id      ON chunk_multi_vector_ids(plaid_doc_id);
    CREATE INDEX idx_cmv_package           ON chunk_multi_vector_ids(package);
    CREATE INDEX ix_node_scores_qname      ON node_scores(qualified_name);
    CREATE INDEX ix_node_scores_package    ON node_scores(package);
    CREATE INDEX ix_decisions_package      ON decision_records(package);
    CREATE TABLE index_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT,
        embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
        activity_summary TEXT, overview_summary TEXT, loadable_grammars TEXT
    );
    CREATE TABLE branches (
        name TEXT PRIMARY KEY, head_sha TEXT NOT NULL, base_name TEXT,
        merge_base_sha TEXT, source TEXT NOT NULL, worktree_path TEXT,
        is_default INTEGER NOT NULL DEFAULT 0, pipeline_hash TEXT NOT NULL,
        indexed_at REAL NOT NULL, last_used_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, retired_at REAL,
        purge_after REAL, pinned INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE branch_files (
        branch TEXT NOT NULL, path TEXT NOT NULL, blob_sha TEXT NOT NULL,
        change_kind TEXT NOT NULL DEFAULT 'unchanged',
        PRIMARY KEY (branch, path)
    );
    CREATE TABLE file_extractions (
        blob_sha TEXT NOT NULL, path TEXT NOT NULL, pipeline_hash TEXT NOT NULL,
        chunk_spans TEXT NOT NULL, tree_json TEXT, members_json TEXT,
        references_json TEXT, created_at REAL NOT NULL,
        PRIMARY KEY (blob_sha, path, pipeline_hash)
    );
    CREATE TABLE branch_chunks (
        branch TEXT NOT NULL, chunk_id INTEGER NOT NULL, source_path TEXT NOT NULL,
        start_line INTEGER, end_line INTEGER, changed INTEGER NOT NULL DEFAULT 0,
        slice TEXT NOT NULL DEFAULT 'tree',
        PRIMARY KEY (branch, chunk_id)
    );
    CREATE INDEX ix_chunks_content_hash   ON chunks(content_hash);
    CREATE INDEX ix_branch_chunks_chunk   ON branch_chunks(chunk_id);
    CREATE INDEX ix_branch_chunks_changed ON branch_chunks(branch, changed);
    CREATE INDEX ix_branch_chunks_slice   ON branch_chunks(branch, slice);
"""

# (table, v17 DDL, the v17 column list, the v17 indexes) for the three tables
# v18 rebuilds with ``branch`` leading the primary key. The column list names
# every v17 column, so the copy back drops only ``branch``.
_V17_KEYED_TABLES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "document_trees",
        "CREATE TABLE document_trees (package TEXT NOT NULL, module TEXT NOT NULL, "
        "tree_json TEXT NOT NULL, content_hash TEXT, updated_at REAL, "
        "PRIMARY KEY (package, module))",
        "package, module, tree_json, content_hash, updated_at",
        ("CREATE INDEX idx_trees_package ON document_trees(package)",),
    ),
    (
        "node_references",
        "CREATE TABLE node_references (from_package TEXT NOT NULL, "
        "from_node_id TEXT NOT NULL, to_name TEXT NOT NULL, to_node_id TEXT, "
        "kind TEXT NOT NULL, PRIMARY KEY (from_package, from_node_id, to_name, kind))",
        "from_package, from_node_id, to_name, to_node_id, kind",
        (
            "CREATE INDEX ix_refs_from ON node_references(from_package, from_node_id)",
            "CREATE INDEX ix_refs_to_name ON node_references(to_name)",
            "CREATE INDEX ix_refs_to_node ON node_references(to_node_id)",
        ),
    ),
    (
        "node_scores",
        "CREATE TABLE node_scores (package TEXT NOT NULL, qualified_name TEXT NOT NULL, "
        "in_degree INTEGER NOT NULL DEFAULT 0, pagerank REAL NOT NULL DEFAULT 0.0, "
        "community INTEGER NOT NULL DEFAULT -1, PRIMARY KEY (package, qualified_name))",
        "package, qualified_name, in_degree, pagerank, community",
        (
            "CREATE INDEX ix_node_scores_qname ON node_scores(qualified_name)",
            "CREATE INDEX ix_node_scores_package ON node_scores(package)",
        ),
    ),
)

# Columns v18 ADDS to a table v17 already had (dropped again on downgrade).
_V18_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("module_members", "branch"),
    ("decision_records", "branch"),
    ("index_metadata", "diff_retain_hash"),
    ("branches", "landing_kind"),
    ("branches", "landed_at"),
    ("branches", "diff_generation_key"),
    ("branches", "merge_evidence"),
    ("branches", "landing_sha"),
    ("branches", "upstream_gone"),
)


def downgrade_bundle_to_v17(db_path: Path) -> None:
    """Rewrite a current-schema bundle into the exact v17 shape, rowids kept.

    Every tree-tier row must carry ``branch = ''`` (what today's write path
    stores) — a branch-stamped duplicate of ``(package, module)`` has no v17
    representation, and the copy's primary key rejects it loudly.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX IF EXISTS ix_branches_landing")
        conn.execute("DROP TABLE IF EXISTS landing_patch_ids")
        for table, column in _V18_ADDED_COLUMNS:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        for table, ddl, columns, indexes in _V17_KEYED_TABLES:
            _rebuild_without_branch(conn, table, ddl, columns, indexes)
        conn.execute(f"PRAGMA user_version = {V17_SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def _rebuild_without_branch(
    conn: sqlite3.Connection, table: str, ddl: str, columns: str, indexes: tuple[str, ...]
) -> None:
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}__v18")
    conn.execute(ddl)
    conn.execute(
        f"INSERT INTO {table} (rowid, {columns}) "
        f"SELECT rowid, {columns} FROM {table}__v18 ORDER BY rowid"
    )
    conn.execute(f"DROP TABLE {table}__v18")  # its v18 indexes go with it
    for statement in indexes:
        conn.execute(statement)


def create_v17_bundle(db_path: Path, seed_sql: str = "") -> None:
    """A fresh v17 database (``V17_DDL``) plus ``seed_sql``, stamped 17."""
    conn = sqlite3.connect(db_path)
    try:
        # One transaction: a script's statements otherwise autocommit one by
        # one, each paying its own fsync.
        conn.executescript(
            f"BEGIN; {V17_DDL} {seed_sql} PRAGMA user_version = {V17_SCHEMA_VERSION}; COMMIT;"
        )
    finally:
        conn.close()


def table_shapes(conn: sqlite3.Connection) -> dict[str, object]:
    """Every table's ``PRAGMA table_info`` rows plus every named index's columns."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    shapes: dict[str, object] = {
        f"table:{t}": [tuple(r) for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables
    }
    for name, table in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    ).fetchall():
        columns = [r[2] for r in conn.execute(f"PRAGMA index_info({name})")]
        shapes[f"index:{name}"] = (table, columns)
    return shapes


def table_rows(conn: sqlite3.Connection, table: str, columns: str) -> list[tuple]:
    """``(rowid, *columns)`` of ``table`` in rowid order — the rows-intact oracle."""
    sql = f"SELECT rowid, {columns} FROM {table} ORDER BY rowid"
    return [tuple(r) for r in conn.execute(sql)]
