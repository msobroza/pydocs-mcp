"""The ``branches`` verbs (spec §6.9, #316): ``--retire / --purge / --pin / --unpin
NAME`` — flags, not positional verbs, because the subcommand's positional
``project`` makes ``branches retire NAME`` ambiguous — and the start-up
maintenance ``_run_indexing`` runs after the working-tree pass."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import _build_parser
from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.db import cache_path_for_project, open_index_database
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchIndexSource, BranchStatus, Chunk
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


def test_the_verbs_are_mutually_exclusive_flags() -> None:
    args = _build_parser().parse_args(["branches", ".", "--retire", "feature/x"])
    assert args.retire == "feature/x" and args.purge is None and args.pin is None
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["branches", ".", "--retire", "a", "--pin", "b"])


def _row(name: str, **overrides: object) -> BranchRecord:
    return BranchRecord(name, "c" * 40, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **overrides)


@pytest.fixture
def bundle(tmp_path: Path) -> tuple[Path, Path]:
    """A project with ``main`` checked out and ``feature/x`` indexed beside it."""
    project = tmp_path / "proj"
    project.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    db = cache / cache_path_for_project(project).name
    open_index_database(db).close()

    async def _seed() -> None:
        async with build_sqlite_uow_factory(db)() as uow:
            await uow.branches.upsert_branch(_row("main", is_default=True))
            await uow.branches.upsert_branch(_row("feature/x"))
            chunk = Chunk.from_test_inputs(
                package=PROJECT_PACKAGE_NAME, module="m", title="t", text="t"
            )
            (chunk_id,) = await uow.chunks.insert_returning_ids((chunk,))
            await uow.branch_chunks.replace_membership(
                "feature/x", [ChunkMembership("feature/x", chunk_id, "a.py")]
            )
            await uow.commit()

    asyncio.run(_seed())
    return project, db


def _branches(project: Path, db: Path, monkeypatch, *flags: str) -> int:
    argv = ["pydocs-mcp", "branches", str(project), "--cache-dir", str(db.parent), *flags]
    monkeypatch.setattr("sys.argv", argv)
    return _cli_main()


def _stored(db: Path, name: str) -> tuple[BranchRecord | None, int]:
    async def _read() -> tuple[BranchRecord | None, int]:
        async with build_sqlite_uow_factory(db)() as uow:
            row = await uow.branches.get_branch(name)
            return row, await uow.branch_chunks.count_for_branch(name)

    return asyncio.run(_read())


def test_pin_and_unpin_flip_the_flag(bundle, capsys, monkeypatch) -> None:
    project, db = bundle
    assert _branches(project, db, monkeypatch, "--pin", "feature/x") == 0
    assert capsys.readouterr().out.strip() == "branches: pin feature/x"
    assert _stored(db, "feature/x")[0].pinned is True
    assert _branches(project, db, monkeypatch, "--unpin", "feature/x") == 0
    assert _stored(db, "feature/x")[0].pinned is False


def test_retire_then_purge_leaves_the_tombstone_only(bundle, capsys, monkeypatch) -> None:
    project, db = bundle
    assert _branches(project, db, monkeypatch, "--retire", "feature/x") == 0
    row, members = _stored(db, "feature/x")
    assert row.status is BranchStatus.INACTIVE and row.purge_after is not None and members == 1
    assert _branches(project, db, monkeypatch, "--purge", "feature/x") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "branches: purge feature/x"
    row, members = _stored(db, "feature/x")
    assert row is not None and members == 0


def test_an_unknown_name_exits_non_zero_listing_the_indexed_branches(
    bundle, capsys, monkeypatch
) -> None:
    project, db = bundle
    assert _branches(project, db, monkeypatch, "--retire", "nope") == 1
    assert capsys.readouterr().out.strip() == (
        "branches: no indexed branch 'nope'; indexed: feature/x, main"
    )


def test_the_checked_out_branch_cannot_be_purged(bundle, capsys, monkeypatch) -> None:
    project, db = bundle
    assert _branches(project, db, monkeypatch, "--purge", "main") == 1
    assert "checked-out branch 'main'" in capsys.readouterr().out
    assert _stored(db, "main")[0].status is BranchStatus.ACTIVE


async def _write_sidecar_at_dim(tq_path: Path, dim: int) -> None:
    import numpy as np

    from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

    async with TurboQuantUnitOfWork(index_path=tq_path, dim=dim, bit_width=4) as uow:
        await uow.add_vectors([1], [np.ones(dim, dtype=np.float32)])
        await uow.commit()


def test_a_flag_flip_never_opens_the_vector_sidecar(bundle, capsys, monkeypatch) -> None:
    """A ``.tq`` built at another ``embedding.dim`` (indexed with ``--config``)
    cannot be opened by the default config: only ``--purge`` frees vectors,
    so only ``--purge`` may fail on it — with the CLI's ``Error:`` line."""
    project, db = bundle
    asyncio.run(_write_sidecar_at_dim(db.with_suffix(".tq"), 16))
    assert _branches(project, db, monkeypatch, "--pin", "feature/x") == 0
    assert _branches(project, db, monkeypatch, "--retire", "feature/x") == 0
    assert _stored(db, "feature/x")[0].pinned is True
    capsys.readouterr()
    assert _branches(project, db, monkeypatch, "--purge", "feature/x") == 1
    err = capsys.readouterr().err
    assert err.startswith("Error:") and "embedding.dim" in err and "Traceback" not in err
    assert _stored(db, "feature/x")[1] == 1  # the failed purge rolled back


@pytest.fixture
def recorded_index_pass(monkeypatch) -> list[object]:
    """``_run_indexing`` over a faked pass; ``events`` records the pass, the
    maintenance build and its run, in order."""
    from types import SimpleNamespace

    import pydocs_mcp.application as application
    import pydocs_mcp.storage.factories as factories
    from pydocs_mcp.application.branch_maintenance import MaintenanceReport
    from pydocs_mcp.application.indexing_service import IndexingStats

    events: list[object] = []

    async def _fake_pass(**kwargs: object) -> IndexingStats:
        events.append("pass")
        return IndexingStats()

    class RecordingMaintenance:
        async def run(self, now: float | None = None) -> MaintenanceReport:
            events.append("maintenance")
            return MaintenanceReport()

    def _fake_build_maintenance(config, db_path, project_root):
        events.append(("build", db_path.name, project_root))
        return RecordingMaintenance()

    bundle = SimpleNamespace(
        **dict.fromkeys(
            (
                "orchestrator",
                "indexing_service",
                "uow_factory",
                "pipeline_hash",
                "check_integrity",
                "rebuild_fts",
                "stamp_metadata",
                "read_prior_state",
                "grammar_fingerprint",
                "write_aggregates",
            ),
        )
    )
    monkeypatch.setattr(factories, "build_project_indexer", lambda *a, **k: bundle)
    monkeypatch.setattr(factories, "build_branch_maintenance", _fake_build_maintenance)
    monkeypatch.setattr(application, "run_index_pass", _fake_pass)
    return events


def _index_argv(tmp_path: Path, verb: str) -> list[str]:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return [verb, str(project), "--cache-dir", str(tmp_path / "cache"), "--skip-deps"]


def test_start_up_maintenance_runs_after_the_working_tree_pass(
    tmp_path, monkeypatch, recorded_index_pass
) -> None:
    """``_run_indexing`` runs the maintenance once the pass returns (the
    extra-branch passes of plan Task 11 will slot in between)."""
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *_index_argv(tmp_path, "index")])
    assert _cli_main() == 0
    project = (tmp_path / "proj").resolve()
    db_name = cache_path_for_project(project).name
    assert recorded_index_pass == ["pass", ("build", db_name, project), "maintenance"]


async def test_a_watch_cycle_reindexes_without_the_branch_maintenance(
    tmp_path, monkeypatch, recorded_index_pass
) -> None:
    """The maintenance belongs to the caller-driven pass: a file save must not
    spawn git work and a write unit of work (spec §6.5 scopes it to start-up
    and base-tip moves)."""
    from pydocs_mcp import __main__ as cli
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.serve import watcher as watcher_mod
    from tests._fakes import FakeObserver

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)
    args = _build_parser().parse_args(_index_argv(tmp_path, "watch"))
    _watcher, on_change = cli._build_watcher_and_callback(args, AppConfig.load().serve.watch)
    await on_change()
    assert recorded_index_pass == ["pass"]
    assert getattr(args, "run_branch_maintenance", True) is True  # the caller's copy
