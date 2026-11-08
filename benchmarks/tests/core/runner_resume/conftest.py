"""Fixtures every runner-resume test shares: reset fakes, a pinned commit, an empty overlay.

The run header's commit comes from real git, pointed at a missing
directory, so the commit identity component is the ``COMMIT_SHA_ENV_VAR``
value each test sets — never whatever checkout the suite happens to live in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR

from ._resume_fakes import reset_resume_fakes
from ._resume_sweep import COMMIT_A


@pytest.fixture(autouse=True)
def _isolated_fakes_and_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_resume_fakes()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, COMMIT_A)
    yield
    reset_resume_fakes()


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "baseline.yaml"
    path.parent.mkdir()
    path.write_text("")
    return path
