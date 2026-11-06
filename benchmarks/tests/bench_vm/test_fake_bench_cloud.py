"""``FakeBenchCloud`` honours the port's generation contract, so launcher tests can trust it."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, StaleGenerationError

from ._fakes import FakeBenchCloud

_URI = "gs://bench-runs/r/run.json"


def test_the_fake_satisfies_the_port() -> None:
    cloud: BenchCloud = FakeBenchCloud(root=Path("unused"))

    assert cloud is not None


def test_generation_zero_creates_and_a_matching_generation_overwrites(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path)

    first = cloud.write_object(_URI, b"one", if_generation_match=0)
    second = cloud.write_object(_URI, b"two", if_generation_match=first)

    stored = cloud.read_object(_URI)
    assert stored is not None
    assert (stored.data, stored.generation) == (b"two", second)
    assert second != first


@pytest.mark.parametrize("stale", [0, 1])
def test_a_write_at_the_wrong_generation_is_refused_and_changes_nothing(
    tmp_path: Path, stale: int
) -> None:
    cloud = FakeBenchCloud(root=tmp_path)
    current = cloud.write_object(_URI, b"newer", if_generation_match=0)

    with pytest.raises(StaleGenerationError, match=_URI):
        cloud.write_object(_URI, b"older", if_generation_match=stale)

    assert cloud.object_data(_URI) == b"newer"
    stored = cloud.read_object(_URI)
    assert stored is not None and stored.generation == current
