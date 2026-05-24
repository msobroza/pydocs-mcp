"""DS-1000 stratified dev/test split tests (fixture-driven).

Pins the ``split`` / ``dev_fraction`` / ``split_seed`` fields added to
``Ds1000Dataset``: a stratified-by-library partition that keeps each
library's proportion in both the ``dev`` and ``test`` slices, is
deterministic under a fixed ``split_seed``, and defaults to ``"all"``
(strict backward-compat — every task is yielded, exactly as before).

Hermetic: backed by ``ds1000_mini.json`` (8 rows — 3 pandas + 2 numpy +
1 matplotlib + 1 sklearn + 1 scipy), no HF network calls.

Rounding note: ``n_dev`` per library is ``round(dev_fraction * n_lib)``
using Python's banker's rounding. The assertions below recompute the
expectation with the SAME formula so the test tracks the implementation
rather than a hand-guessed integer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval.datasets.ds1000 import Ds1000Dataset

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ds1000_mini.json"

# The split default — referenced so the backward-compat assertion can't
# silently drift if the default changes.
_DEFAULT_DEV_FRACTION = 0.2


async def _task_ids(dataset: Ds1000Dataset) -> set[str]:
    return {t.task_id async for t in dataset.tasks()}


async def _tasks(dataset: Ds1000Dataset) -> list:
    return [t async for t in dataset.tasks()]


async def test_split_all_yields_every_task() -> None:
    """``split="all"`` (the default) is a strict no-op vs the pre-split
    loader — it yields all 8 fixture tasks, identical to the no-arg load."""
    default_ids = await _task_ids(Ds1000Dataset(fixture_path=FIXTURE_PATH))
    all_ids = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    assert len(default_ids) == 8
    assert all_ids == default_ids


async def test_dev_and_test_are_disjoint_and_complete_partition() -> None:
    """``dev`` and ``test`` partition the full task set: their union equals
    the ``all`` set and their intersection is empty (no task lost, no task
    in both)."""
    all_ids = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    dev_ids = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="dev"),
    )
    test_ids = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="test"),
    )
    assert dev_ids & test_ids == set()  # disjoint
    assert dev_ids | test_ids == all_ids  # complete


async def test_stratification_preserves_per_library_proportions() -> None:
    """For EACH library present in the fixture, the ``dev`` slice holds
    ``round(dev_fraction * n_lib)`` of that library's tasks and ``test``
    holds the remainder — so both slices keep the corpus's per-library
    proportions. ``metadata["library"]`` is the normalized (lowercase
    PyPI-canonical) name."""
    all_tasks = await _tasks(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    dev_tasks = await _tasks(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="dev"),
    )
    test_tasks = await _tasks(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="test"),
    )

    def _by_lib(tasks: list) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in tasks:
            lib = t.metadata["library"]
            counts[lib] = counts.get(lib, 0) + 1
        return counts

    all_by_lib = _by_lib(all_tasks)
    dev_by_lib = _by_lib(dev_tasks)
    test_by_lib = _by_lib(test_tasks)

    assert all_by_lib  # fixture sanity: at least one library present
    for lib, n_lib in all_by_lib.items():
        # SAME formula as the impl (banker's rounding) — the test tracks
        # the implementation rather than a hand-guessed number.
        expected_dev = round(_DEFAULT_DEV_FRACTION * n_lib)
        assert dev_by_lib.get(lib, 0) == expected_dev, lib
        # dev + test per library exactly reconstructs the library's count.
        assert dev_by_lib.get(lib, 0) + test_by_lib.get(lib, 0) == n_lib, lib


async def test_split_is_deterministic_under_fixed_seed() -> None:
    """Two independent ``split="dev"`` loads with the same ``split_seed``
    yield identical task_id sets — the seeded shuffle is reproducible."""
    first = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="dev", split_seed=0),
    )
    second = await _task_ids(
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="dev", split_seed=0),
    )
    assert first == second


async def test_split_composes_with_library_filter() -> None:
    """``split`` operates on the ALREADY-filtered rows, so it composes with
    ``library_filter``. Filtering to pandas then taking ``test`` yields only
    pandas test tasks, and dev+test of that filtered subset reconstruct the
    3 pandas rows."""
    pandas_all = await _task_ids(
        Ds1000Dataset(
            fixture_path=FIXTURE_PATH, library_filter=("pandas",), split="all",
        ),
    )
    pandas_dev = await _task_ids(
        Ds1000Dataset(
            fixture_path=FIXTURE_PATH, library_filter=("pandas",), split="dev",
        ),
    )
    pandas_test_tasks = await _tasks(
        Ds1000Dataset(
            fixture_path=FIXTURE_PATH, library_filter=("pandas",), split="test",
        ),
    )
    assert len(pandas_all) == 3
    assert all(t.metadata["library"] == "pandas" for t in pandas_test_tasks)
    pandas_test = {t.task_id for t in pandas_test_tasks}
    # The split partitions the filtered subset, not the whole corpus.
    assert pandas_dev & pandas_test == set()
    assert pandas_dev | pandas_test == pandas_all
    # 3 pandas rows -> round(0.2*3)=1 dev, 2 test.
    assert len(pandas_dev) == round(_DEFAULT_DEV_FRACTION * 3)
    assert len(pandas_test) == 3 - round(_DEFAULT_DEV_FRACTION * 3)


async def test_invalid_split_raises_value_error() -> None:
    """A ``split`` value outside {all,dev,test} is a caller bug — the loader
    rejects it with a ``ValueError`` (here ``"train"``, the HF split name a
    confused caller might reach for)."""
    with pytest.raises(ValueError):
        Ds1000Dataset(fixture_path=FIXTURE_PATH, split="train")
