"""AC19 (spec 2026-07-11-cli-mcp-docs-audit, D3): `serve.watch.enabled` is
wired — either switch (CLI `--watch` or the YAML key) enables watch mode.

The tests monkeypatch `_cmd_serve`'s phase seams (indexing, watch loop,
plain serve) so no index is built and no server runs — pure dispatch checks.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import pydocs_mcp.__main__ as main_mod


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    """Patch _cmd_serve's seams; return (run, calls) where calls records
    which phase-2 path was taken."""
    calls: list[str] = []
    monkeypatch.setattr(main_mod, "_run_cmd", lambda coro, verbose: (coro.close(), 0)[1])
    monkeypatch.setattr(main_mod, "_project_and_db", lambda args: (tmp_path, tmp_path / "x.db"))
    monkeypatch.setattr(
        main_mod, "_run_blocking", lambda fn, verbose: (calls.append("watch"), 0)[1]
    )
    monkeypatch.setattr(
        main_mod,
        "_serve_run",
        lambda args, db_path, workspace, db_paths: (calls.append("plain"), 0)[1],
    )

    def run(*, watch_flag: bool, enabled_in_yaml: bool) -> list[str]:
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            f"serve:\n  watch:\n    enabled: {str(enabled_in_yaml).lower()}\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            project_dir=str(tmp_path),
            watch=watch_flag,
            config=overlay,  # argparse gives a Path (--config has type=Path)
            verbose=False,
            workspace=None,
            db_paths=None,
        )
        assert main_mod._cmd_serve(args) == 0
        return calls

    return run


def test_yaml_enabled_starts_watch_without_flag(dispatch) -> None:
    assert dispatch(watch_flag=False, enabled_in_yaml=True) == ["watch"]


def test_flag_starts_watch_with_key_false(dispatch) -> None:
    assert dispatch(watch_flag=True, enabled_in_yaml=False) == ["watch"]


def test_both_off_serves_plain(dispatch) -> None:
    assert dispatch(watch_flag=False, enabled_in_yaml=False) == ["plain"]


def test_yaml_default_stays_false() -> None:
    """The shipped default is unchanged — enabling watch remains opt-in
    (watch-promotion spec Non-goal 1 / this spec's AC19 tail)."""
    from pydocs_mcp.retrieval.config import AppConfig

    assert AppConfig().serve.watch.enabled is False
