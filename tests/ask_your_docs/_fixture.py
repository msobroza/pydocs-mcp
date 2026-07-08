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
) -> Path:
    docstrings = docstrings or {}
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version={user_version}")
    conn.execute("INSERT INTO index_metadata VALUES (?, ?)", (project, 1.0))
    conn.execute("INSERT INTO packages VALUES ('__project__', '')")
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
    conn.commit()
    conn.close()
    return path
