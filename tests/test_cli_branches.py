"""The ``branches`` verb (spec §6.9, P0: list only)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_listing import (
    BranchSummary,
    format_branch_summaries,
    list_branch_summaries,
)
from pydocs_mcp.db import (
    BRANCH_TABLES_SCHEMA_VERSION,
    cache_path_for_project,
    open_index_database,
)
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, BranchStatus, Chunk
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


def _seed(db: Path) -> None:
    open_index_database(db).close()

    async def _run() -> None:
        async with build_sqlite_uow_factory(db)() as uow:
            await uow.branches.upsert_branch(
                BranchRecord(
                    "main",
                    "c" * 40,
                    BranchIndexSource.WORKING_TREE,
                    "p",
                    100.0,
                    100.0,
                    is_default=True,
                )
            )
            await uow.branches.replace_files("main", [BranchFile("main", "pkg/a.py", "b")])
            ids = await uow.chunks.insert_returning_ids(
                (
                    Chunk.from_test_inputs(
                        package=PROJECT_PACKAGE_NAME, module="m", title="t", text="t"
                    ),
                )
            )
            await uow.branch_chunks.replace_membership(
                "main", [ChunkMembership("main", ids[0], "pkg/a.py")]
            )
            await uow.commit()

    asyncio.run(_run())


def _user_version(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _stamp_user_version(db: Path, version: int) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


def _run_cli(argv: list[str], monkeypatch) -> int:
    # ``main()`` reads ``sys.argv`` (no argv parameter) — same driving pattern
    # as ``tests/test_cli_link.py``.
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


def test_list_branch_summaries_counts_files_and_chunks(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    _seed(db)
    summaries = asyncio.run(list_branch_summaries(build_sqlite_uow_factory(db)))
    assert summaries == (BranchSummary("main", BranchStatus.ACTIVE, "c" * 40, 100.0, True, 1, 1),)


def test_format_renders_one_line_per_branch() -> None:
    text = format_branch_summaries(
        (
            BranchSummary("main", BranchStatus.ACTIVE, "c" * 40, 100.0, True, 3, 42),
            BranchSummary("feature", BranchStatus.MERGED, "d" * 40, 100.0, False, 1, 2),
        ),
        now=100.0 + 3 * 3600,
    )
    assert "main" in text and "ccccccc" in text and "3h" in text and "42" in text and "*" in text
    # The sha must be TRUNCATED, not merely present: an 8th character would
    # mean the ``[:_SHORT_SHA_LEN]`` slice never ran.
    assert "c" * 8 not in text
    # ``*`` must be CONDITIONAL on is_default — a single default-only row
    # cannot tell "marks the default" apart from "always prints a star".
    main_line = next(line for line in text.splitlines() if "main" in line)
    feature_line = next(line for line in text.splitlines() if "feature" in line)
    assert main_line.startswith("*")
    assert "*" not in feature_line


def test_cli_lists_branches_for_a_project(tmp_path: Path, capsys, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db = cache_dir / cache_path_for_project(project).name
    _seed(db)
    code = _run_cli(["branches", str(project), "--cache-dir", str(cache_dir)], monkeypatch)
    out = capsys.readouterr().out
    assert code == 0 and "main" in out and "ccccccc" in out


def test_cli_hints_when_no_index_exists(tmp_path: Path, capsys, monkeypatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    code = _run_cli(["branches", str(project), "--cache-dir", str(tmp_path / "empty")], monkeypatch)
    # The hint must be ACTIONABLE — it names the project to index, so a bare
    # "run pydocs-mcp index" with the path dropped does not satisfy it.
    assert code == 1 and f"pydocs-mcp index {project.resolve()}" in capsys.readouterr().out


def test_cli_gates_a_pre_branch_bundle_without_migrating_it(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """A pre-v16 bundle is reported, NOT migrated (the verb advertises read-only).

    Migrating it here would clear ``packages.content_hash`` for ``__project__``
    and so silently force a full project re-extraction on the operator's next
    ``pydocs-mcp index`` — a listing command must never cost that.
    """
    project = tmp_path / "proj"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db = cache_dir / cache_path_for_project(project).name
    stale = BRANCH_TABLES_SCHEMA_VERSION - 1
    _stamp_user_version(db, stale)

    code = _run_cli(["branches", str(project), "--cache-dir", str(cache_dir)], monkeypatch)
    out = capsys.readouterr().out

    assert code == 1
    assert "predates branch indexing" in out
    assert f"pydocs-mcp index {project.resolve()}" in out
    assert _user_version(db) == stale, "the listing verb migrated the bundle — it must not write"


def test_cli_lists_a_branch_era_bundle_after_a_later_schema_bump(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """The gate is ``BRANCH_TABLES_SCHEMA_VERSION``, not the moving
    ``SCHEMA_VERSION``: a bundle stamped at the version that introduced the
    branch tables stays listable once a later bump lands (P1 stamps v17).

    ``SCHEMA_VERSION`` is patched to simulate that bump, so the case bites TODAY
    — pinned against the moving constant it would only start failing after P1.
    """
    project = tmp_path / "proj"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db = cache_dir / cache_path_for_project(project).name
    _seed(db)  # seeded BEFORE the patch so the tables carry today's shape
    _stamp_user_version(db, BRANCH_TABLES_SCHEMA_VERSION)
    monkeypatch.setattr("pydocs_mcp.db.SCHEMA_VERSION", BRANCH_TABLES_SCHEMA_VERSION + 1)

    code = _run_cli(["branches", str(project), "--cache-dir", str(cache_dir)], monkeypatch)
    out = capsys.readouterr().out

    assert code == 0 and "main" in out
    assert "predates branch indexing" not in out


def test_cli_rejects_a_non_sqlite_file_without_destroying_it(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Garbage at the cache path is reported, never unlinked and recreated."""
    project = tmp_path / "proj"
    project.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    db = cache_dir / cache_path_for_project(project).name
    garbage = b"this is not a sqlite database at all"
    db.write_bytes(garbage)

    code = _run_cli(["branches", str(project), "--cache-dir", str(cache_dir)], monkeypatch)
    out = capsys.readouterr().out

    assert code == 1 and "is not a pydocs-mcp index bundle" in out
    assert db.read_bytes() == garbage, "the listing verb destroyed the file — it must not"
