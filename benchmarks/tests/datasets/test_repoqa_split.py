"""RepoQA stratified dev/test split tests (fixture-driven).

Pins the ``split`` / ``dev_fraction`` / ``split_seed`` fields added to
``RepoQADataset``: the same stratified partition DS-1000 uses, stratified
by ``repo`` (the RepoQA analogue of DS-1000's library), deterministic
under a fixed ``split_seed``, and defaulting to ``"all"`` (strict
backward-compat — every needle is yielded, exactly as before).

Hermetic: backed by ``repoqa_mini.json`` (2 repos with multiple needles
each so stratification is observable), no network calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval.datasets.repoqa import RepoQADataset

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"

# The split default — referenced so the backward-compat assertion can't
# silently drift if the default changes.
_DEFAULT_DEV_FRACTION = 0.2


async def _task_ids(dataset: RepoQADataset) -> set[str]:
    return {t.task_id async for t in dataset.tasks()}


async def _tasks(dataset: RepoQADataset) -> list:
    return [t async for t in dataset.tasks()]


async def test_split_all_yields_every_needle() -> None:
    """``split="all"`` (the default) is a strict no-op vs the pre-split
    loader — it yields every needle, identical to the no-arg load."""
    default_ids = await _task_ids(RepoQADataset(fixture_path=FIXTURE_PATH))
    all_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    assert all_ids == default_ids
    assert len(all_ids) >= 2  # fixture sanity: more than one needle


async def test_dev_and_test_are_disjoint_and_complete_partition() -> None:
    """``dev`` and ``test`` partition the full needle set: their union
    equals the ``all`` set and their intersection is empty."""
    all_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    dev_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="dev"),
    )
    test_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="test"),
    )
    assert dev_ids & test_ids == set()  # disjoint
    assert dev_ids | test_ids == all_ids  # complete


async def test_stratification_preserves_per_repo_proportions() -> None:
    """For EACH repo in the fixture, the ``dev`` slice holds
    ``round(dev_fraction * n_repo)`` of that repo's needles and ``test``
    holds the remainder — so both slices keep the per-repo proportions."""
    all_tasks = await _tasks(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="all"),
    )
    dev_tasks = await _tasks(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="dev"),
    )
    test_tasks = await _tasks(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="test"),
    )

    def _by_repo(tasks: list) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in tasks:
            repo = t.metadata["repo"]
            counts[repo] = counts.get(repo, 0) + 1
        return counts

    all_by_repo = _by_repo(all_tasks)
    dev_by_repo = _by_repo(dev_tasks)
    test_by_repo = _by_repo(test_tasks)

    assert len(all_by_repo) >= 2  # fixture sanity: stratification observable
    for repo, n_repo in all_by_repo.items():
        # SAME formula as the impl (banker's rounding).
        expected_dev = round(_DEFAULT_DEV_FRACTION * n_repo)
        assert dev_by_repo.get(repo, 0) == expected_dev, repo
        assert dev_by_repo.get(repo, 0) + test_by_repo.get(repo, 0) == n_repo, repo


async def test_split_is_deterministic_under_fixed_seed() -> None:
    """Two independent ``split="dev"`` loads with the same ``split_seed``
    yield identical task_id sets."""
    first = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="dev", split_seed=0),
    )
    second = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="dev", split_seed=0),
    )
    assert first == second


def test_invalid_split_raises_value_error() -> None:
    """A ``split`` outside {all,dev,test,small_test,small_dev} is a caller
    bug — rejected at construction with a ``ValueError``."""
    with pytest.raises(ValueError):
        RepoQADataset(fixture_path=FIXTURE_PATH, split="train")


async def test_small_test_is_capped_subset_of_test() -> None:
    """``split="small_test"`` yields a fixed-size stratified subsample of the
    held-out ``test`` tail: a subset of ``test``, capped at
    ``min(small_test_size, |test|)``."""
    test_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="test"),
    )
    small_ids = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_test",
            small_test_size=1,
        ),
    )
    assert small_ids <= test_ids  # subset of held-out test
    assert len(small_ids) == min(1, len(test_ids))


async def test_small_test_is_deterministic_under_fixed_seed() -> None:
    """Two ``small_test`` loads with the same seed yield identical ids."""
    first = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_test",
            small_test_size=2,
            split_seed=0,
        ),
    )
    second = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_test",
            small_test_size=2,
            split_seed=0,
        ),
    )
    assert first == second


async def test_small_dev_is_capped_subset_of_dev() -> None:
    """``split="small_dev"`` yields a fixed-size stratified subsample of the
    ``dev`` head: a subset of ``dev``, capped at
    ``min(small_test_size, |dev|)``. dev_fraction=0.5 so the fixture's dev
    head (2 + 1 needles) is big enough to observe the cap."""
    dev_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="dev", dev_fraction=0.5),
    )
    small_ids = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_dev",
            dev_fraction=0.5,
            small_test_size=2,
        ),
    )
    assert small_ids <= dev_ids  # subset of the dev head
    assert len(small_ids) == min(2, len(dev_ids))


async def test_small_dev_never_touches_the_test_tail() -> None:
    """``small_dev`` is the burn-free iteration slice — it must be disjoint
    from the held-out ``test`` tail by construction."""
    test_ids = await _task_ids(
        RepoQADataset(fixture_path=FIXTURE_PATH, split="test", dev_fraction=0.5),
    )
    small_ids = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_dev",
            dev_fraction=0.5,
            small_test_size=2,
        ),
    )
    assert small_ids & test_ids == set()


async def test_small_dev_is_deterministic_under_fixed_seed() -> None:
    """Two ``small_dev`` loads with the same seed yield identical ids —
    the split-seed determinism contract extends to the new split."""
    first = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_dev",
            small_test_size=1,
            split_seed=0,
        ),
    )
    second = await _task_ids(
        RepoQADataset(
            fixture_path=FIXTURE_PATH,
            split="small_dev",
            small_test_size=1,
            split_seed=0,
        ),
    )
    assert first == second
