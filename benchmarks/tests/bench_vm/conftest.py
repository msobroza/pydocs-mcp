"""Fixtures for the bench VM launcher and supervisor tests: a fixture git repo, a fake cloud."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.provider_secrets import PROVIDER_KEYS

from ._fakes import FakeBenchCloud
from ._git_fixture import make_fixture_repo
from ._launch_harness import LaunchHarness
from ._supervise_harness import SuperviseHarness


@pytest.fixture(autouse=True)
def _no_provider_key_from_the_developer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every provider-key variable, so a real key reaches no child, log or message.

    Every value a bench VM test handles is synthetic (spec amendment 2026-11-08, AC-R21).
    """
    for name in list(os.environ):
        if name in PROVIDER_KEYS or name.endswith("_API_KEY"):
            monkeypatch.delenv(name)


@pytest.fixture
def harness(tmp_path: Path) -> LaunchHarness:
    repo = make_fixture_repo(tmp_path / "git")
    launch_harness = LaunchHarness(
        repo=repo, specs_dir=tmp_path / "runs", cloud=FakeBenchCloud(root=tmp_path / "gcs")
    )
    launch_harness.publish_images(repo.head())
    return launch_harness


@pytest.fixture
def supervise_harness(
    harness: LaunchHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SuperviseHarness:
    return SuperviseHarness(launch=harness, root=tmp_path / "vm", monkeypatch=monkeypatch)
