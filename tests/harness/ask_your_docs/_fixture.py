"""Build a tiny read-only-safe pydocs bundle for graph tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE index_metadata (project_name TEXT, indexed_at REAL);
CREATE TABLE packages (name TEXT, embedding_model TEXT);
CREATE TABLE module_members (
    id INTEGER PRIMARY KEY, package TEXT, module TEXT, name TEXT, kind TEXT,
    signature TEXT, return_annotation TEXT, parameters TEXT, docstring TEXT
);
CREATE TABLE node_references (
    from_package TEXT, from_node_id TEXT, to_name TEXT, to_node_id TEXT, kind TEXT
);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY, package TEXT, module TEXT DEFAULT '',
    title TEXT, text TEXT, origin TEXT, content_hash TEXT, qualified_name TEXT
);
"""

# The v16 branch tables (db.py _V16_STATEMENTS), created unless the test wants
# a pre-v16 bundle (with_branch_tables=False -> the E8 degrade path).
_BRANCH_SCHEMA = """
CREATE TABLE branches (
    name TEXT PRIMARY KEY, head_sha TEXT NOT NULL, base_name TEXT, merge_base_sha TEXT,
    source TEXT NOT NULL, worktree_path TEXT, is_default INTEGER NOT NULL DEFAULT 0,
    pipeline_hash TEXT NOT NULL, indexed_at REAL NOT NULL, last_used_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', merged_into TEXT, retired_at REAL,
    purge_after REAL, pinned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE branch_chunks (
    branch TEXT NOT NULL, chunk_id INTEGER NOT NULL, source_path TEXT NOT NULL,
    start_line INTEGER, end_line INTEGER, changed INTEGER NOT NULL DEFAULT 0,
    slice TEXT NOT NULL DEFAULT 'tree', PRIMARY KEY (branch, chunk_id)
);
"""

# (name, head_sha, base_name, is_default, status, merged_into)
BranchRow = tuple[str, str, str | None, int, str, str | None]


def make_bundle(
    path: Path,
    *,
    project: str = "demo",
    user_version: int = 99,
    members: list[tuple[str, str, str]] = (),
    refs: list[tuple[str, str, str]] = (),
    markdown: list[tuple[str, str, str]] = (),
    decisions: list[tuple[str, str]] = (),
    docstrings: dict[str, str] | None = None,
    branches: list[BranchRow] = (),
    branch_chunks: list[tuple[str, int]] = (),
    with_branch_tables: bool = True,
    packages: list[str] = (),
) -> Path:
    docstrings = docstrings or {}
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version={user_version}")
    conn.execute("INSERT INTO index_metadata VALUES (?, ?)", (project, 1.0))
    conn.execute("INSERT INTO packages VALUES ('__project__', '')")
    # Dependency packages: SqliteBundleReader.packages() filters __project__ OUT, so a
    # bundle built without these has an EMPTY package pool and no Package selectbox.
    for package in packages:
        conn.execute("INSERT INTO packages VALUES (?, '')", (package,))
    for module, name, kind in members:
        node_id = f"{module}.{name}"
        conn.execute(
            "INSERT INTO module_members (package, module, name, kind, signature, "
            "return_annotation, parameters, docstring) VALUES "
            "('__project__', ?, ?, ?, ?, '', '', ?)",
            (module, name, kind, f"def {name}(...)", docstrings.get(node_id, "")),
        )
    for from_id, to_id, kind in refs:
        to_name = (to_id or "").rsplit(".", 1)[-1]
        conn.execute(
            "INSERT INTO node_references VALUES ('__project__', ?, ?, ?, ?)",
            (from_id, to_name, to_id, kind),
        )
    for i, (file, title, text) in enumerate(markdown):
        conn.execute(
            "INSERT INTO chunks (id, package, module, title, text, origin, "
            "content_hash, qualified_name) VALUES (?, '__project__', ?, ?, ?, "
            "'markdown_section', '', ?)",
            (1000 + i, file, title, text, f"{file}#{i}"),
        )
    for i, (title, text) in enumerate(decisions):
        conn.execute(
            "INSERT INTO chunks (id, package, module, title, text, origin, "
            "content_hash, qualified_name) VALUES (?, '__project__', '', ?, ?, "
            "'decision_record', '', ?)",
            (2000 + i, title, text, f"decision:{i}"),
        )
    if with_branch_tables:
        conn.executescript(_BRANCH_SCHEMA)
        for name, head_sha, base_name, is_default, status, merged_into in branches:
            conn.execute(
                "INSERT INTO branches (name, head_sha, base_name, source, is_default, "
                "pipeline_hash, indexed_at, last_used_at, status, merged_into) VALUES "
                "(?, ?, ?, 'working_tree', ?, 'ph', 1.0, 1.0, ?, ?)",
                (name, head_sha, base_name, is_default, status, merged_into),
            )
        for branch, chunk_id in branch_chunks:
            conn.execute(
                "INSERT INTO branch_chunks (branch, chunk_id, source_path) VALUES (?, ?, 'x.py')",
                (branch, chunk_id),
            )
    conn.commit()
    conn.close()
    return path
