"""Where the remote lane runs (#318 review): beside the ref watcher only — its
fast-forwards and anyone's fetch reach the index and the signal through the
watcher's events — with layer 1 only where a response can carry it, and one
log line when a configured remote layer will not run, under ``serve``,
``serve --watch`` and ``watch`` alike."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pytest

import pydocs_mcp.__main__ as main_mod
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.serve.refresh_wiring import RefreshWiring, build_refresh_parts
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git
from tests.serve._remote_fakes import logged_events

pytestmark = requires_git

_AUTO_FETCH = "  remote: {auto_fetch: {enabled: true}}\n"
_REF_WATCH_OFF = "  ref_watch: {enabled: false}\n"


@pytest.fixture(autouse=True)
def _sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "app.py", "x = 1\n", "init")
    return root


def _config(tmp_path: Path, yaml: str) -> AppConfig:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(yaml, encoding="utf-8")
    return AppConfig.load(explicit_path=overlay)


async def _nothing() -> None:
    return None


def _parts(root: Path, config: AppConfig, *, answers_requests: bool = True):
    wiring = RefreshWiring(
        config=config,
        project_root=root,
        db_path=root.parent / "proj.db",
        reindex_working_tree=_nothing,
        bundle_factory=lambda: pytest.fail("no pass runs here"),
        extra_branches=ExtraBranchRequest(),
        answers_requests=answers_requests,
    )
    return build_refresh_parts(wiring, locate_gitdir(root))


def test_with_the_ref_watcher_off_no_lane_runs_and_a_configured_layer_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Without the watcher a fast-forward would never be reindexed and the
    signal never refreshed after a fetch; ``serve --watch`` and ``watch`` now
    agree with plain ``serve``, which runs no refresh loop at all."""
    config = _config(tmp_path, "git:\n" + _REF_WATCH_OFF + _AUTO_FETCH)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        parts = _parts(_repo(tmp_path), config)
    assert parts.remote_lane is None
    assert logged_events(caplog, "remote_sync_unavailable") == [
        {"event": "remote_sync_unavailable", "reason": "git.ref_watch.enabled is false"}
    ]


def test_the_default_signal_alone_logs_nothing_when_it_cannot_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path, "git:\n" + _REF_WATCH_OFF)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        assert _parts(_repo(tmp_path), config).remote_lane is None
    assert logged_events(caplog, "remote_sync_unavailable") == []


@pytest.mark.parametrize(
    ("yaml", "answers_requests", "layer_one"),
    [
        ("", True, True),
        ("git:\n  remote: {behind_hint: false}\n", True, False),
        # The ADR 0007 rule flag: no response would carry the signal.
        ("output:\n  suggestions: {behind_upstream: false}\n", True, False),
        # The standalone ``watch``: no server in the process reads the board.
        ("", False, False),
    ],
)
def test_layer_one_runs_only_where_a_response_can_carry_it(
    tmp_path: Path, yaml: str, answers_requests: bool, layer_one: bool
) -> None:
    config = _config(tmp_path, yaml)
    lane = _parts(_repo(tmp_path), config, answers_requests=answers_requests).remote_lane
    assert lane is not None and lane.config.behind_hint is layer_one


def test_plain_serve_without_a_refresh_loop_says_the_configured_lane_will_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("git:\n" + _REF_WATCH_OFF + _AUTO_FETCH, encoding="utf-8")
    monkeypatch.setattr(main_mod, "_run_cmd", lambda coro, verbose: (coro.close(), 0)[1])
    monkeypatch.setattr(main_mod, "_project_and_db", lambda args: (tmp_path, tmp_path / "x.db"))
    monkeypatch.setattr(main_mod, "_serve_run", lambda args, db_path, workspace, db_paths: 0)
    args = argparse.Namespace(
        project_dir=str(tmp_path),
        watch=False,
        config=overlay,
        verbose=False,
        workspace=None,
        db_paths=None,
    )
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        assert main_mod._cmd_serve(args) == 0
    (unavailable,) = logged_events(caplog, "remote_sync_unavailable")
    assert unavailable["reason"] == "git.ref_watch.enabled is false"
