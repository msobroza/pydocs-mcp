"""``index / serve / watch --branch NAME`` (repeatable) and ``--all-branches``
(spec §6.9, #310): the flags parse, the extra-branch passes run after the
working-tree pass and before the branch maintenance, a file-change reindex
never repeats them, and an unknown name exits 1 naming the local branches."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp import __main__ as cli
from pydocs_mcp.__main__ import _build_parser
from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_pass import BranchPassOutcome
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.retrieval.config import AppConfig
from tests._fakes import FakeGitRepository, FakeObserver, make_fake_uow_factory


@pytest.mark.parametrize("verb", ["index", "serve", "watch"])
def test_every_indexing_verb_takes_the_branch_flags(verb: str) -> None:
    args = _build_parser().parse_args([verb, ".", "--branch", "feature/x", "--branch", "rel/1"])
    assert args.branches == ["feature/x", "rel/1"] and args.all_branches is False
    args = _build_parser().parse_args([verb, ".", "--all-branches"])
    assert args.branches is None and args.all_branches is True


async def test_a_file_change_reindex_never_repeats_the_extra_branch_passes(
    tmp_path: Path, monkeypatch
) -> None:
    from pydocs_mcp.serve import watcher as watcher_mod

    monkeypatch.setattr(watcher_mod, "_load_watchdog", lambda: FakeObserver)
    seen: list[argparse.Namespace] = []

    async def _fake_run_indexing(ns: argparse.Namespace) -> None:
        seen.append(ns)

    monkeypatch.setattr(cli, "_run_indexing", _fake_run_indexing)
    args = argparse.Namespace(
        project=str(tmp_path),
        cache_dir=None,
        config=None,
        force=False,
        branches=["feature/x"],
        all_branches=True,
    )
    _watcher, on_change = cli._build_watcher_and_callback(args, AppConfig.load().serve.watch)
    await on_change()
    assert (seen[0].branches, seen[0].all_branches) == (None, False)
    assert (args.branches, args.all_branches) == (["feature/x"], True)


@dataclass
class _FakeIndexer:
    git: FakeGitRepository
    events: list[object]
    uow_factory: object = field(default_factory=make_fake_uow_factory)

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS
    ) -> BranchPassOutcome:
        self.events.append(("branch", name))
        return BranchPassOutcome(1, 1, 0, 0, 1, 0)


@pytest.fixture
def recorded_passes(monkeypatch) -> list[object]:
    """``_run_indexing`` over a faked working-tree pass, branch indexer and
    maintenance; ``events`` records what ran, in order."""
    import pydocs_mcp.application as application
    import pydocs_mcp.storage.factories as factories
    from pydocs_mcp.application.branch_maintenance import MaintenanceReport
    from pydocs_mcp.application.indexing_service import IndexingStats

    events: list[object] = []
    git = FakeGitRepository(
        branch="main", refs={"refs/heads/main": "a" * 40, "refs/heads/feature/x": "b" * 40}
    )

    async def _fake_pass(**kwargs: object) -> IndexingStats:
        events.append("pass")
        return IndexingStats()

    class _Maintenance:
        async def run(self, now: float | None = None) -> MaintenanceReport:
            events.append("maintenance")
            return MaintenanceReport()

    def _fake_build_indexer(config, project_root, bundle):
        events.append("build-branch-indexer")
        return _FakeIndexer(git, events)

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
            )
        )
    )
    monkeypatch.setattr(factories, "build_project_indexer", lambda *a, **k: bundle)
    monkeypatch.setattr(factories, "build_branch_indexer", _fake_build_indexer)
    monkeypatch.setattr(factories, "build_branch_maintenance", lambda *a: _Maintenance())
    monkeypatch.setattr(application, "run_index_pass", _fake_pass)
    return events


def _index(tmp_path: Path, monkeypatch, *flags: str) -> int:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    argv = ["index", str(project), "--cache-dir", str(tmp_path / "cache"), "--skip-deps", *flags]
    monkeypatch.setattr("sys.argv", ["pydocs-mcp", *argv])
    return _cli_main()


def test_the_extra_branches_run_between_the_pass_and_the_maintenance(
    tmp_path, monkeypatch, recorded_passes
) -> None:
    assert _index(tmp_path, monkeypatch, "--branch", "feature/x") == 0
    assert recorded_passes == [
        "build-branch-indexer",
        "pass",
        ("branch", "feature/x"),
        "maintenance",
    ]


def test_no_flag_builds_no_branch_indexer(tmp_path, monkeypatch, recorded_passes) -> None:
    assert _index(tmp_path, monkeypatch) == 0
    assert recorded_passes == ["pass", "maintenance"]


def test_an_unknown_branch_exits_1_naming_the_local_branches(
    tmp_path, monkeypatch, capsys, recorded_passes
) -> None:
    assert _index(tmp_path, monkeypatch, "--branch", "nope") == 1
    err = capsys.readouterr().err
    assert "no local branch named 'nope'; local branches: feature/x, main" in err
    # Checked before the working-tree pass: a typo costs one git call, not a
    # full index (#310 review).
    assert recorded_passes == ["build-branch-indexer"]
