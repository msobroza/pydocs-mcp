"""Base resolution (R14, §6.5), tracking selection and LRU eviction (§6.9, D13)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_policy import (
    BaseBranch,
    lru_evictions,
    plumbing_base_tip,
    resolve_base_branch,
    select_tracked_branches,
)
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingKind
from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig, GitConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository

A, B, C = "a" * 40, "b" * 40, "c" * 40


def test_auto_base_prefers_the_remote_head_symref_and_its_tracking_tip() -> None:
    git = FakeGitRepository(
        symrefs={"refs/remotes/origin/HEAD": "refs/remotes/origin/trunk"},
        refs={"refs/remotes/origin/trunk": A, "refs/heads/trunk": B, "refs/heads/main": C},
    )
    assert resolve_base_branch(git, GitConfig()) == BaseBranch(
        "trunk", A, "refs/remotes/origin/trunk"
    )


def test_auto_base_falls_back_to_main_then_master_with_the_local_tip() -> None:
    git = FakeGitRepository(refs={"refs/heads/master": B})
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("master", B, None)
    git = FakeGitRepository(refs={"refs/heads/master": B, "refs/heads/main": C})
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("main", C, None)
    assert resolve_base_branch(FakeGitRepository(), GitConfig()) is None


def test_explicit_base_name_still_prefers_its_tracking_ref() -> None:
    git = FakeGitRepository(refs={"refs/remotes/origin/develop": A, "refs/heads/develop": B})
    cfg = GitConfig(branches={"base": "develop"})
    assert resolve_base_branch(git, cfg) == BaseBranch("develop", A, "refs/remotes/origin/develop")


def test_the_remote_head_keeps_a_base_name_that_contains_a_slash() -> None:
    # ``rsplit("/")`` would turn release/1.0 into "1.0" and resolve nothing.
    git = FakeGitRepository(
        symrefs={"refs/remotes/origin/HEAD": "refs/remotes/origin/release/1.0"},
        refs={"refs/remotes/origin/release/1.0": A},
    )
    assert resolve_base_branch(git, GitConfig()) == BaseBranch(
        "release/1.0", A, "refs/remotes/origin/release/1.0"
    )


def test_a_dangling_remote_head_falls_through_to_main() -> None:
    # The symref names a pruned tracking ref and no local branch of that name
    # exists: R14's next candidate answers instead of "no base".
    git = FakeGitRepository(
        symrefs={"refs/remotes/origin/HEAD": "refs/remotes/origin/gone"},
        refs={"refs/heads/main": C},
    )
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("main", C, None)


def test_a_remote_head_outside_the_remote_namespace_is_ignored() -> None:
    git = FakeGitRepository(
        symrefs={"refs/remotes/origin/HEAD": "refs/heads/trunk"},
        refs={"refs/heads/trunk": A, "refs/heads/main": C},
    )
    assert resolve_base_branch(git, GitConfig()) == BaseBranch("main", C, None)


def test_main_resolves_from_its_tracking_ref_without_a_local_branch() -> None:
    # A clone of one feature branch: origin/main exists, local main does not.
    git = FakeGitRepository(refs={"refs/remotes/origin/main": A, "refs/heads/feature/x": B})
    assert resolve_base_branch(git, GitConfig()) == BaseBranch(
        "main", A, "refs/remotes/origin/main"
    )


def test_the_configured_remote_name_decides_every_tracking_ref() -> None:
    git = FakeGitRepository(
        symrefs={"refs/remotes/upstream/HEAD": "refs/remotes/upstream/trunk"},
        refs={"refs/remotes/upstream/trunk": A, "refs/remotes/origin/main": B},
    )
    cfg = GitConfig(remote={"name": "upstream"})
    assert resolve_base_branch(git, cfg) == BaseBranch("trunk", A, "refs/remotes/upstream/trunk")


def _events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]


def test_no_base_is_logged_once_and_never_raised(caplog: pytest.LogCaptureFixture) -> None:
    git = FakeGitRepository(refs={"refs/heads/trunk": A})
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        assert resolve_base_branch(git, GitConfig()) is None
    (event,) = _events(caplog)
    assert event == {
        "event": "base_branch_unresolved",
        "configured_base": "auto",
        "remote": "origin",
        "tried": ["main", "master"],
    }
    # ``auto`` found nothing to anchor on: informational, not an operator error.
    assert caplog.records[0].levelno == logging.INFO


def test_an_explicit_base_that_resolves_nothing_warns_and_does_not_fall_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    git = FakeGitRepository(refs={"refs/heads/main": C})
    cfg = GitConfig(branches={"base": "develop"})
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        assert resolve_base_branch(git, cfg) is None
    (event,) = _events(caplog)
    assert event["tried"] == ["develop"] and event["configured_base"] == "develop"
    assert caplog.records[0].levelno == logging.WARNING


def test_a_resolved_base_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    git = FakeGitRepository(refs={"refs/heads/main": C})
    with caplog.at_level(logging.DEBUG, logger="pydocs-mcp"):
        resolve_base_branch(git, GitConfig())
    assert _events(caplog) == []


def test_plumbing_base_tip_reads_the_tracking_ref_without_git(tmp_path: Path) -> None:
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "remotes" / "origin").mkdir(parents=True)
    (gitdir / "refs" / "remotes" / "origin" / "main").write_text(A + "\n", encoding="utf-8")
    assert plumbing_base_tip(gitdir, BaseBranch("main", B, "refs/remotes/origin/main")) == A
    (gitdir / "refs" / "heads").mkdir()
    (gitdir / "refs" / "heads" / "main").write_text(C + "\n", encoding="utf-8")
    assert plumbing_base_tip(gitdir, BaseBranch("main", B, None)) == C


def test_plumbing_base_tip_degrades_to_none_on_unreadable_plumbing(tmp_path: Path) -> None:
    # The request path must never raise: a corrupt (non-UTF-8) ref file makes
    # ``resolve_ref`` raise UnicodeDecodeError.
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "refs" / "heads" / "main").write_bytes(b"\xff\xfe")
    assert plumbing_base_tip(gitdir, BaseBranch("main", B, None)) is None
    assert plumbing_base_tip(tmp_path / "missing", BaseBranch("main", B, None)) is None


def test_tracking_selection_expands_reserved_words_and_globs_in_order() -> None:
    local = ("main", "feature/x", "release/1", "release/2")
    cfg = GitBranchesConfig(track=["checked_out", "release/*"])
    assert select_tracked_branches(cfg, local, "feature/x") == (
        "feature/x",
        "release/1",
        "release/2",
    )
    assert select_tracked_branches(GitBranchesConfig(track=["all_local"]), local, None) == local
    assert select_tracked_branches(
        GitBranchesConfig(track=["checked_out", "main"]), local, "main"
    ) == ("main",)


def test_tracking_selection_skips_a_detached_head_and_unknown_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Windows' case-folding normcase, forced on every OS: ``fnmatch.fnmatch``
    # applies it, ``fnmatchcase`` must not — git branch names are case-sensitive
    # (PR CI runs on Linux only, where normcase is a no-op).
    monkeypatch.setattr(os.path, "normcase", str.lower)
    local = ("main", "Feature/X")
    cfg = GitBranchesConfig(track=["checked_out", "nope", "feature/*"])
    # Detached HEAD: nothing is checked out.
    assert select_tracked_branches(cfg, local, None) == ()


def _row(name: str, used: float, **kw) -> BranchRecord:
    return BranchRecord(name, A, BranchIndexSource.GIT_OBJECTS, "p", used, used, **kw)


def test_lru_evictions_skip_pinned_protected_and_landing_units() -> None:
    rows = (
        _row("old", 1.0),
        _row("older", 0.5),
        _row("pinned", 0.1, pinned=True),
        _row("checked", 0.2),
        _row("d" * 40, 0.0, landing_kind=LandingKind.SINGLE_COMMIT),
        _row("new", 9.0),
        _row("gone", 0.3, status=BranchStatus.DELETED),
    )
    assert lru_evictions(rows, 1, protected={"checked"}) == ("old", "older")
    assert lru_evictions(rows, 3, protected={"checked"}) == ()


def test_lru_evictions_count_inactive_rows_and_skip_merged_ones() -> None:
    rows = (
        _row("idle", 2.0, status=BranchStatus.INACTIVE),
        _row("landed", 1.0, status=BranchStatus.MERGED),
        _row("fresh", 3.0),
    )
    assert lru_evictions(rows, 1, protected=()) == ("idle",)
