"""The graph explorer reads the served branch of a multi-branch bundle (#313).

A bundle holding ``main`` (the default) and ``feature/x`` must not draw both
branches' members, edges and documents as one graph: the page shows the
branch its caption names — the served default — until its branch selector
drives the reads. A bundle holding one branch keeps its SQL, so its rows stay
exactly what they were.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from pydocs_mcp.db import open_index_database
from pydocs_mcp.harness.ask_your_docs.bundle import SqliteBundleReader

_OWN = "__project__"
_HEAD = "a" * 40


def _branch(conn: sqlite3.Connection, name: str, *, default: bool) -> None:
    conn.execute(
        "INSERT INTO branches (name, head_sha, source, is_default, pipeline_hash, indexed_at, "
        "last_used_at) VALUES (?, ?, 'working_tree', ?, 'p', 1.0, 1.0)",
        (name, _HEAD, int(default)),
    )


def _rows_on(conn: sqlite3.Connection, branch: str, name: str, chunk_id: int) -> None:
    conn.execute(
        "INSERT INTO module_members (package, module, name, kind, signature, docstring, branch) "
        "VALUES (?, 'app.core', ?, 'def', '()', '', ?)",
        (_OWN, name, branch),
    )
    conn.execute(
        "INSERT INTO node_references (branch, from_package, from_node_id, to_name, to_node_id, "
        "kind) VALUES (?, ?, ?, 'alpha', 'app.core.alpha', 'calls')",
        (branch, _OWN, f"app.core.{name}"),
    )
    conn.execute(
        "INSERT INTO chunks (id, package, module, title, text, origin) "
        "VALUES (?, ?, 'docs/guide.md', ?, 't', 'markdown_section')",
        (chunk_id, _OWN, f"{name} section"),
    )
    conn.execute(
        "INSERT INTO branch_chunks (branch, chunk_id, source_path) VALUES (?, ?, 'docs/guide.md')",
        (branch, chunk_id),
    )


def _bundle(tmp_path: Path, *branches: str) -> SqliteBundleReader:
    db = tmp_path / "demo_0123456789.db"
    open_index_database(db).close()
    with closing(sqlite3.connect(db)) as conn:
        for i, (branch, member) in enumerate(zip(branches, ("beta", "gamma"), strict=False)):
            _branch(conn, branch, default=i == 0)
            _rows_on(conn, branch, member, i + 1)
        conn.commit()
    return SqliteBundleReader(db)


def test_a_multi_branch_bundle_draws_the_served_branch_only(tmp_path: Path) -> None:
    reader = _bundle(tmp_path, "main", "feature/x")
    assert [name for _module, name, _kind in reader.member_rows()] == ["beta"]
    assert [row[0] for row in reader.reference_rows()] == ["app.core.beta"]
    assert [row[0] for row in reader.references_of("app.core.alpha")] == ["app.core.beta"]
    assert reader.find_member("gamma", "app.core") is None
    assert [title for _id, title in reader.markdown_sections("docs/guide.md")] == ["beta section"]


def test_a_single_branch_bundle_reads_every_row_as_before(tmp_path: Path) -> None:
    reader = _bundle(tmp_path, "main")
    assert [name for _module, name, _kind in reader.member_rows()] == ["beta"]
    assert reader.find_member("beta", "app.core") == ("beta", "()", "")
    assert reader.markdown_files() == ["docs/guide.md"]
