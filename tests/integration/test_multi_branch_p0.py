"""P0 end to end: index a real git checkout, verify the branch tables, re-run,
edit, switch branch — through the same composition root the CLI uses."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application import run_index_pass
from pydocs_mcp.db import cache_path_for_project, open_index_database
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_project_indexer

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _git(root: Path, *args: str) -> None:
    # Inherit the environment and override only identity + HOME. A replacement
    # env would also replace PATH, and on POSIX subprocess resolves the program
    # through the PASSED env's PATH — so a hardcoded PATH makes the fixture die
    # with FileNotFoundError (rather than skip) wherever git lives elsewhere,
    # and on Windows it also drops SystemRoot/PATHEXT, which git needs.
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
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "a.py").write_text(
        'def alpha():\n    """A."""\n    return 1\n', encoding="utf-8"
    )
    (root / "pkg" / "b.py").write_text(
        'def beta():\n    """B."""\n    return 2\n', encoding="utf-8"
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _index(root: Path, db: Path, config: AppConfig) -> None:
    # Schema creation before the composition root, exactly as ``__main__``
    # does it — ``build_project_indexer`` documents the caller as responsible.
    open_index_database(db).close()
    bundle = build_project_indexer(config, db, use_inspect=False, inspect_depth=None)
    asyncio.run(
        run_index_pass(
            orchestrator=bundle.orchestrator,
            indexing_service=bundle.indexing_service,
            pipeline_hash=bundle.pipeline_hash,
            project=root,
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model_name,
            embedding_dim=config.embedding.dim,
            force=False,
            include_project_source=True,
            include_dependencies=False,
            workers=1,
            check_integrity=bundle.check_integrity,
            rebuild_fts=bundle.rebuild_fts,
            stamp_metadata=bundle.stamp_metadata,
            write_aggregates=bundle.write_aggregates,
        )
    )


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _count(db: Path, from_clause: str) -> int:
    return int(_rows(db, f"SELECT COUNT(*) FROM {from_clause}")[0][0])


def _run_cli(argv: list[str], monkeypatch) -> int:
    # ``main()`` reads ``sys.argv`` (no argv parameter) — same driving pattern
    # as ``tests/test_cli_link.py`` and ``tests/test_cli_branches.py``.
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


def test_first_pass_stamps_branch_manifest_membership_and_cache(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    _index(root, db, AppConfig.load())
    assert _rows(db, "SELECT name, is_default, source, length(head_sha) FROM branches") == [
        ("main", 1, "working_tree", 40)
    ]
    files = dict(_rows(db, "SELECT path, length(blob_sha) FROM branch_files"))
    assert files == {"pkg/__init__.py": 40, "pkg/a.py": 40, "pkg/b.py": 40}
    project_chunks = _count(db, f"chunks WHERE package='{PROJECT_PACKAGE_NAME}'")
    # Every project chunk is a member of the stamped branch — a partial swap
    # (some chunks unassigned) would still be > 0, so equality is the assertion.
    assert _count(db, "branch_chunks WHERE branch='main'") == project_chunks > 0
    assert _count(db, "file_extractions") >= 2  # a.py and b.py carry chunks


def test_unchanged_pass_is_cached_and_edit_updates_membership(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    membership_sql = "SELECT chunk_id, source_path, start_line FROM branch_chunks ORDER BY 1"
    before = _rows(db, membership_sql)
    _index(root, db, config)
    # Same chunk ids, not merely the same count: a re-extract that reinserted
    # every chunk would keep the count and renumber the rows.
    assert _rows(db, membership_sql) == before

    (root / "pkg" / "b.py").write_text(
        'def gamma():\n    """G."""\n    return 3\n', encoding="utf-8"
    )
    _index(root, db, config)
    titles = {
        r[0] for r in _rows(db, f"SELECT title FROM chunks WHERE package='{PROJECT_PACKAGE_NAME}'")
    }
    assert any("gamma" in t for t in titles) and not any("beta" in t for t in titles)
    blobs = dict(_rows(db, "SELECT path, blob_sha FROM branch_files"))
    assert _rows(
        db,
        "SELECT COUNT(*) FROM file_extractions "
        f"WHERE path='pkg/b.py' AND blob_sha='{blobs['pkg/b.py']}'",
    ) == [(1,)]


def test_switching_branch_replaces_the_working_tree_branch_record(tmp_path: Path) -> None:
    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "c.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feature")
    _index(root, db, config)
    assert _rows(db, "SELECT name FROM branches") == [("feature/x",)]  # P0: one per checkout
    assert _count(db, "branch_chunks WHERE branch='main'") == 0
    # The new branch TOOK OVER the membership — a sweep that deleted every row
    # and stamped none would satisfy the two assertions above on its own.
    assert _count(db, "branch_chunks WHERE branch='feature/x'") > 0
    assert "pkg/c.py" in dict(_rows(db, "SELECT path, blob_sha FROM branch_files"))


def test_non_git_project_uses_the_sentinel_branch(tmp_path: Path) -> None:
    root, db = tmp_path / "plain", tmp_path / "p.db"
    (root / "m").mkdir(parents=True)
    (root / "m" / "x.py").write_text("def x():\n    return 0\n", encoding="utf-8")
    _index(root, db, AppConfig.load())
    assert _rows(db, "SELECT name, head_sha FROM branches") == [(NON_GIT_BRANCH_NAME, "")]
    assert _rows(db, "SELECT DISTINCT blob_sha FROM branch_files") == [("",)]
    # Membership is still stamped without git — only the blob-keyed extraction
    # cache is skipped (no blob id to key it on).
    assert _count(db, f"branch_chunks WHERE branch='{NON_GIT_BRANCH_NAME}'") > 0
    assert _count(db, "file_extractions") == 0


def test_cli_branches_lists_the_stamped_branch(tmp_path: Path, capsys, monkeypatch) -> None:
    root = _project(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    _index(root, cache / cache_path_for_project(root).name, AppConfig.load())
    assert _run_cli(["branches", str(root), "--cache-dir", str(cache)], monkeypatch) == 0
    out = capsys.readouterr().out
    # "* main" — the default marker, not a bare substring a mangled row would
    # also satisfy — plus a non-zero chunks column (the last field of the row),
    # so the case proves the verb read the bundle's counts rather than echoing
    # a name it was handed.
    assert "* main" in out
    row = next(line for line in out.splitlines() if line.startswith("* main"))
    chunk_count = row.split()[-1]
    assert chunk_count.isdigit() and int(chunk_count) > 0, out


def test_v15_bundle_upgrade_re_extracts_once_without_re_embedding(tmp_path: Path) -> None:
    from tests.test_db_schema_v16_migration import _V15_SCRIPT  # the v15 fixture script

    root, db = _project(tmp_path), tmp_path / "p.db"
    config = AppConfig.load()
    _index(root, db, config)
    # Row IDENTITY, not cardinality: a regression that deleted every project
    # chunk on the migration pass and re-inserted + re-embedded all of them
    # keeps COUNT(embedded=1) at 2 and would pass a count comparison. Only the
    # surviving ids + content hashes separate "kept" from "re-embedded".
    identity_sql = (
        f"SELECT id, content_hash FROM chunks WHERE package='{PROJECT_PACKAGE_NAME}' ORDER BY id"
    )
    chunks_before = _rows(db, identity_sql)
    vectors_before = _count(db, "chunks WHERE embedded=1")
    # Downgrade the stamp only: the tables exist, but a 15-stamped open must
    # clear __project__'s content_hash and re-extract (spec §6.1 migration).
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    conn.close()
    open_index_database(db).close()
    assert _rows(db, "SELECT content_hash FROM packages WHERE name='__project__'") == [(None,)]
    _index(root, db, config)
    assert _rows(db, identity_sql) == chunks_before
    assert _count(db, "chunks WHERE embedded=1") == vectors_before
    assert _count(db, "branch_chunks") > 0
    assert _V15_SCRIPT  # keeps the import meaningful for the linter
