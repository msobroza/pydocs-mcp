"""Fixtures for the bench VM launcher tests: a real fixture git repo and a fake cloud."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._fakes import FakeBenchCloud
from ._git_fixture import make_fixture_repo
from ._launch_harness import LaunchHarness


@pytest.fixture
def harness(tmp_path: Path) -> LaunchHarness:
    repo = make_fixture_repo(tmp_path / "git")
    launch_harness = LaunchHarness(
        repo=repo, specs_dir=tmp_path / "runs", cloud=FakeBenchCloud(root=tmp_path / "gcs")
    )
    launch_harness.publish_images(repo.head())
    return launch_harness
