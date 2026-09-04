"""The ``branches`` verb (spec §6.9, P0: list only)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_listing import (
    BranchSummary,
    format_branch_summaries,
    list_branch_summaries,
)
from pydocs_mcp.db import cache_path_for_project, open_index_database
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
        (BranchSummary("main", BranchStatus.ACTIVE, "c" * 40, 100.0, True, 3, 42),),
        now=100.0 + 3 * 3600,
    )
    assert "main" in text and "ccccccc" in text and "3h" in text and "42" in text and "*" in text


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
    assert code == 1 and "pydocs-mcp index" in capsys.readouterr().out
