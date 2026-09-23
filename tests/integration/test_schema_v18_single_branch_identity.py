"""Schema v18 on a single-branch project answers exactly as v17 did (#305).

A real git project is indexed once by the current code, then the bundle is
copied twice: ``fresh`` stays the v18 bundle a new install writes; ``legacy`` is
rewritten into the exact v17 shape (``tests/_schema_v17_bundle.py``) — what
every 0.8.x user holds — and reopened through the v17 → v18 migration, which
stamps its project rows with the default branch.

The two must serve byte-identical ``text`` and ``items`` on search, symbol,
references, context and why (AC 1, 2). Then the project changes and BOTH are
re-indexed by today's write path, which stamps the working tree's branch and
replaces that branch's rows (#307): the migrated bundle — stamped rows plus
rows that path writes — must end with the fresh bundle's rows, no key
collision, no duplicate, no lost row, and the same answers. An unchanged pass
after the migration re-extracts and re-embeds nothing (AC 4, spec §6.1).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from contextlib import closing
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._fakes import CountingEmbedder
from tests._index_fixture import index_project_to_db, run_pass_with_embedder
from tests._schema_v17_bundle import downgrade_bundle_to_v17

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_MOD_PY = '''\
"""The core module."""


class Alpha:
    """A class the user module calls."""

    def run(self) -> int:
        return beta()


def beta() -> int:
    """A function Alpha.run calls."""
    return 1
'''

_USER_PY = '''\
"""Calls into the core module."""

from pkg.mod import Alpha, beta


def use() -> int:
    """Uses both names."""
    return Alpha().run() + beta()
'''

_ADR = """\
# 1. Store the index in SQLite

Status: Accepted
Date: 2026-01-02

We keep pkg.mod.Alpha state in SQLite because it ships with Python.
"""

_ADR_ADDED = """\
# 2. Rank with PageRank

Status: Accepted
Date: 2026-02-03

pkg.mod.beta results are ranked by PageRank over the reference graph.
"""

# Per table, the identity a v17 row carried (its primary key, or for the two
# id-keyed tables the natural key): the no-duplicate oracle after the reindex.
_IDENTITY_COLUMNS = {
    "document_trees": "package, module",
    "node_references": "from_package, from_node_id, to_name, kind",
    "node_scores": "package, qualified_name",
    "module_members": "package, module, name, kind",
    "decision_records": "package, title",
}
# Row content compared across the two bundles — every column but the branch
# key and the wall-clock stamps a reindex writes at different instants.
_CONTENT_COLUMNS = {
    "document_trees": "package, module, tree_json, content_hash",
    "node_references": "from_package, from_node_id, to_name, to_node_id, kind",
    "node_scores": "package, qualified_name, in_degree, pagerank, community",
    "module_members": "id, package, module, name, kind, signature, return_annotation, "
    "parameters, docstring",
    "decision_records": "id, package, title, status, source, confidence, evidence, "
    "affected_files, affected_qnames, superseded_by, verification, structured",
    "chunks": "id, package, module, title, text, content_hash, embedded, qualified_name, "
    "decision_id, source_path, start_line, end_line",
}


def _git(root: Path, *args: str) -> None:
    # Same environment rule as tests/integration/test_multi_branch_p0.py: keep
    # PATH (git must resolve), override only identity and HOME.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
        "HOME": str(root),
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text(_MOD_PY, encoding="utf-8")
    (root / "pkg" / "user.py").write_text(_USER_PY, encoding="utf-8")
    (root / "docs" / "adr" / "0001-sqlite.md").write_text(_ADR, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _edit_project(root: Path) -> None:
    """A change that rewrites every tree-tier table: an edited function, a new
    one, and a second ADR next to the kept first one."""
    (root / "pkg" / "mod.py").write_text(
        _MOD_PY.replace("return 1\n", "return gamma()\n")
        + '\n\ndef gamma() -> int:\n    """New in the edit."""\n    return 3\n',
        encoding="utf-8",
    )
    (root / "docs" / "adr" / "0002-pagerank.md").write_text(_ADR_ADDED, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "edit")


def _copy_bundle(source: Path, target: Path) -> Path:
    """The ``.db`` through SQLite's backup API (WAL content included) plus the
    ``.tq`` vector sidecar, which sits beside it under the same stem."""
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    for sidecar in source.parent.glob(f"{source.stem}.*"):
        if sidecar.suffix not in (".db", ".db-wal", ".db-shm"):
            shutil.copy2(sidecar, target.with_suffix(sidecar.suffix))
    return target


def _answers(db: Path) -> dict[str, tuple[str, tuple]]:
    """``(text, items)`` of every indexed tool the tree tier feeds — the two
    fields spec R7 holds byte-identical (``meta`` may differ by ``branch``)."""
    router, _services = build_routers(AppConfig.load(), db_path=db, surface="mcp")
    calls = {
        "search": (router.search_codebase, SearchInput(query="alpha run beta")),
        "search_decision": (router.search_codebase, SearchInput(query="sqlite", kind="decision")),
        "symbol": (router.get_symbol, SymbolInput(target="pkg.mod.Alpha")),
        "symbol_source": (router.get_symbol, SymbolInput(target="pkg.mod.beta", depth="source")),
        "callers": (router.get_references, ReferencesInput(target="pkg.mod.beta")),
        "callees": (
            router.get_references,
            ReferencesInput(target="pkg.user.use", direction="callees"),
        ),
        "impact": (
            router.get_references,
            ReferencesInput(target="pkg.mod.beta", direction="impact"),
        ),
        "governed_by": (
            router.get_references,
            ReferencesInput(target="pkg.mod.Alpha", direction="governed_by"),
        ),
        "context": (
            router.get_context,
            ContextInput(targets=["pkg.mod.Alpha", "pkg.user.use"]),
        ),
        "why": (router.get_why, WhyInput(query="why sqlite")),
    }
    answers = {}
    for name, (tool, payload) in calls.items():
        response = asyncio.run(tool(payload))
        answers[name] = (response.text, response.items)
    return answers


def _rows(db: Path, table: str, columns: str) -> list[tuple]:
    """Order-free row set; ``repr`` keys because NULL and text share columns."""
    with closing(sqlite3.connect(db)) as conn:
        rows = [tuple(r) for r in conn.execute(f"SELECT {columns} FROM {table}")]
    return sorted(rows, key=repr)


def _duplicates(db: Path, table: str) -> list[tuple]:
    identity = _IDENTITY_COLUMNS[table]
    sql = f"SELECT {identity}, COUNT(*) FROM {table} GROUP BY {identity} HAVING COUNT(*) > 1"
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


def _project_branches(db: Path, table: str) -> set[str]:
    column = "from_package" if table == "node_references" else "package"
    sql = f"SELECT DISTINCT branch FROM {table} WHERE {column} = ?"
    with closing(sqlite3.connect(db)) as conn:
        return {r[0] for r in conn.execute(sql, (PROJECT_PACKAGE_NAME,))}


def _seed_node_scores(db: Path) -> None:
    """The ``[graph]`` extra is not installed in the suite, so the scores pass
    no-ops; seeded rows keep the rebuilt ``node_scores`` table under test."""
    with closing(sqlite3.connect(db)) as conn:
        conn.executemany(
            "INSERT INTO node_scores (package, qualified_name, in_degree, pagerank, community) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (PROJECT_PACKAGE_NAME, "pkg.mod.beta", 2, 0.4, 0),
                (PROJECT_PACKAGE_NAME, "pkg.mod.Alpha", 1, 0.3, 0),
                (PROJECT_PACKAGE_NAME, "pkg.user.use", 0, 0.1, 1),
            ],
        )
        conn.commit()


@pytest.fixture
def bundles(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(project, fresh v18 bundle, migrated v17 bundle)`` from ONE index pass."""
    root = _project(tmp_path)
    indexed = index_project_to_db(root, tmp_path / "indexed.db")
    _seed_node_scores(indexed)
    fresh = _copy_bundle(indexed, tmp_path / "fresh.db")
    legacy = _copy_bundle(indexed, tmp_path / "legacy.db")
    downgrade_bundle_to_v17(legacy)
    open_index_database(legacy).close()  # the v17 → v18 step, as every open runs it
    return root, fresh, legacy


def test_the_migrated_bundle_stamps_the_default_branch_on_project_rows(bundles) -> None:
    _root, _fresh, legacy = bundles
    for table in _IDENTITY_COLUMNS:
        assert _project_branches(legacy, table) == {"main"}, table


def test_a_migrated_v17_bundle_answers_byte_identically(bundles) -> None:
    _root, fresh, legacy = bundles
    fresh_answers = _answers(fresh)
    assert all(text for text, _ in fresh_answers.values())  # every call answered
    assert _answers(legacy) == fresh_answers


def test_reindexing_after_the_migration_matches_a_fresh_bundle(bundles) -> None:
    root, fresh, legacy = bundles
    _edit_project(root)
    index_project_to_db(root, fresh)
    index_project_to_db(root, legacy)
    for table, columns in _CONTENT_COLUMNS.items():
        assert _rows(legacy, table, columns) == _rows(fresh, table, columns), table
    for table in _IDENTITY_COLUMNS:
        # A stamped row left beside its rewritten twin would show up here.
        assert _duplicates(legacy, table) == _duplicates(fresh, table) == [], table
    answers = _answers(fresh)
    assert any("gamma" in text for text, _ in answers.values())  # the edit is served
    assert _answers(legacy) == answers


def test_an_unchanged_pass_after_the_migration_re_embeds_nothing(bundles) -> None:
    root, _fresh, legacy = bundles
    hashes_sql = "SELECT name, content_hash FROM packages"
    with closing(sqlite3.connect(legacy)) as conn:
        before = dict(conn.execute(hashes_sql))
    counting = CountingEmbedder()
    stats = run_pass_with_embedder(AppConfig.load(), legacy, root, embedder=counting)
    assert stats.project_indexed is False  # the package-level cache hit held
    assert [c for c in counting.calls if c[0] == "embed_chunks"] == []
    with closing(sqlite3.connect(legacy)) as conn:
        assert dict(conn.execute(hashes_sql)) == before
