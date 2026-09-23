"""#308 end to end: every working-tree pass stamps the base branch (spec §6.5, R14)
through the real subprocess adapter and the same composition root the CLI uses."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.retrieval.config import AppConfig
from tests.integration.test_multi_branch_p0 import _git, _index, _project, _rows, _run_cli

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

_BASE_SQL = "SELECT name, base_name, merge_base_sha FROM branches"


def _sha(root: Path, ref: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", ref], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _feature_branch(root: Path) -> None:
    _git(root, "checkout", "-q", "-b", "feature/x")
    (root / "pkg" / "c.py").write_text("def delta():\n    return 4\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feature")


def _base_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    messages = (r.getMessage() for r in caplog.records)
    return [json.loads(m) for m in messages if m.startswith('{"event": "base_branch_')]


def test_a_bundle_indexed_from_a_feature_branch_carries_base_main(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = _project(tmp_path)
    main_head = _sha(root, "main")
    _feature_branch(root)
    cache = tmp_path / "cache"
    cache.mkdir()
    db = cache / cache_path_for_project(root).name
    _index(root, db, AppConfig.load())
    # No remote: origin/HEAD is unset, so R14 falls back to the local main,
    # and the merge-base is main's tip (the fork point).
    assert _rows(db, _BASE_SQL) == [("feature/x", "main", main_head)]

    assert _run_cli(["branches", str(root), "--cache-dir", str(cache)], monkeypatch) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:2] == ["branch", "base"]
    assert lines[1].split()[:3] == ["*", "feature/x", "main"]


def test_the_remote_head_symref_names_the_base_and_its_tracking_tip(tmp_path: Path) -> None:
    root = _project(tmp_path)
    trunk_tip = _sha(root, "main")
    # A remote-tracking ref plus origin/HEAD, exactly as `git clone` leaves them.
    _git(root, "update-ref", "refs/remotes/origin/trunk", trunk_tip)
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    _feature_branch(root)
    db = tmp_path / "p.db"
    _index(root, db, AppConfig.load())
    assert _rows(db, _BASE_SQL) == [("feature/x", "trunk", trunk_tip)]


def test_without_origin_head_or_main_the_base_falls_back_to_master(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _git(root, "branch", "-q", "-m", "main", "master")
    master_head = _sha(root, "master")
    _feature_branch(root)
    db = tmp_path / "p.db"
    _index(root, db, AppConfig.load())
    assert _rows(db, _BASE_SQL) == [("feature/x", "master", master_head)]


def test_no_candidate_base_is_logged_and_the_pass_still_stamps_the_branch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys, monkeypatch
) -> None:
    root = _project(tmp_path)
    _git(root, "branch", "-q", "-m", "main", "trunk")
    cache = tmp_path / "cache"
    cache.mkdir()
    db = cache / cache_path_for_project(root).name
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        _index(root, db, AppConfig.load())
    assert _rows(db, _BASE_SQL) == [("trunk", None, None)]
    assert _base_events(caplog) == [
        {
            "event": "base_branch_unresolved",
            "configured_base": "auto",
            "remote": "origin",
            "tried": ["main", "master"],
        }
    ]
    # No base stamped anywhere: the listing keeps its pre-#308 columns.
    assert _run_cli(["branches", str(root), "--cache-dir", str(cache)], monkeypatch) == 0
    assert capsys.readouterr().out.splitlines()[0].split()[:2] == ["branch", "status"]
