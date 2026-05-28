"""Unit tests for the shared ``stratified_split`` helper + ``validate_split``.

These pin the dataset-agnostic partition logic directly (plain dict rows,
no dataset object): ``"all"`` is a strict no-op; ``dev``/``test`` form a
disjoint-and-complete partition; each stratum keeps its proportion
(``n_dev == round(dev_fraction * len(group))`` per stratum); the seeded
shuffle is deterministic; empty input round-trips to empty; and a bad
``split`` value raises ``ValueError``.

The helper is the single source of truth for BOTH ``Ds1000Dataset`` and
``RepoQADataset`` — testing it directly (rather than only via either
dataset) guards the contract regardless of which loader calls it.
"""

from __future__ import annotations

import pytest
from benchmarks.eval.datasets._split import (
    VALID_SPLITS,
    stratified_split,
    validate_split,
)


def _rows() -> list[dict[str, object]]:
    """10 rows over 2 strata: 6 in ``"a"``, 4 in ``"b"``. Each row has a
    unique ``key`` so the sort key is total and the partition observable."""
    rows: list[dict[str, object]] = []
    for i in range(6):
        rows.append({"stratum": "a", "key": f"a{i:02d}"})
    for i in range(4):
        rows.append({"stratum": "b", "key": f"b{i:02d}"})
    return rows


def _split(rows: list[dict[str, object]], split: str, *, seed: int = 0) -> list:
    return stratified_split(
        rows,
        split=split,
        dev_fraction=0.2,
        seed=seed,
        stratum_of=lambda r: r["stratum"],
        sort_key=lambda r: r["key"],
    )


def test_all_returns_input_unchanged() -> None:
    """``"all"`` is a strict no-op — returns the SAME rows (identity of
    contents and order), not a partitioned subset."""
    rows = _rows()
    result = _split(rows, "all")
    assert result == rows


def test_dev_and_test_partition_all_disjoint_and_complete() -> None:
    """``dev`` ∪ ``test`` reconstructs the full row set and ``dev`` ∩
    ``test`` is empty — no row lost, no row duplicated."""
    rows = _rows()
    dev = _split(rows, "dev")
    test = _split(rows, "test")
    dev_keys = {r["key"] for r in dev}
    test_keys = {r["key"] for r in test}
    all_keys = {r["key"] for r in rows}
    assert dev_keys & test_keys == set()  # disjoint
    assert dev_keys | test_keys == all_keys  # complete


def test_per_stratum_proportions() -> None:
    """Each stratum is split independently: ``n_dev`` for that stratum is
    ``round(dev_fraction * len(group))`` and dev+test reconstructs it."""
    rows = _rows()
    dev = _split(rows, "dev")
    test = _split(rows, "test")

    def by_stratum(items: list) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in items:
            counts[r["stratum"]] = counts.get(r["stratum"], 0) + 1
        return counts

    dev_counts = by_stratum(dev)
    test_counts = by_stratum(test)
    # stratum "a": 6 rows -> round(0.2*6)=1 dev, 5 test
    assert dev_counts.get("a", 0) == round(0.2 * 6)
    assert dev_counts.get("a", 0) + test_counts.get("a", 0) == 6
    # stratum "b": 4 rows -> round(0.2*4)=1 dev, 3 test
    assert dev_counts.get("b", 0) == round(0.2 * 4)
    assert dev_counts.get("b", 0) + test_counts.get("b", 0) == 4


def test_deterministic_under_fixed_seed() -> None:
    """Same seed -> byte-identical partition (the shuffle is reproducible)."""
    rows = _rows()
    first = [r["key"] for r in _split(rows, "dev", seed=7)]
    second = [r["key"] for r in _split(rows, "dev", seed=7)]
    assert first == second


def test_empty_input_returns_empty() -> None:
    """Empty rows -> empty result for every split (no IndexError on the
    slice, no spurious group)."""
    for split in ("all", "dev", "test", "small_test"):
        assert _split([], split) == []


def test_validate_split_accepts_valid() -> None:
    for split in VALID_SPLITS:
        validate_split(split)  # must not raise


def test_validate_split_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        validate_split("train")


def test_stratified_split_rejects_invalid_split() -> None:
    """The helper validates its own ``split`` argument (defense in depth —
    callers also validate at construction, but the helper never trusts a
    bad value)."""
    with pytest.raises(ValueError):
        _split(_rows(), "train")


def _small_test(
    rows: list[dict[str, object]], *, size: int, seed: int = 0,
) -> list:
    return stratified_split(
        rows,
        split="small_test",
        dev_fraction=0.2,
        seed=seed,
        stratum_of=lambda r: r["stratum"],
        sort_key=lambda r: r["key"],
        small_test_size=size,
    )


def test_small_test_hits_target_size_exactly() -> None:
    """``small_test`` apportions EXACTLY ``small_test_size`` rows when the
    test tail is large enough — Hamilton's largest-remainder method, not a
    per-stratum ``round()`` that would over/under-shoot the target."""
    rows = _rows()  # test tail = 5 ("a") + 3 ("b") = 8
    assert len(_small_test(rows, size=4)) == 4


def test_small_test_is_subset_of_test() -> None:
    """``small_test`` ⊂ ``test`` — it samples the held-out tail, never dev."""
    rows = _rows()
    test_keys = {r["key"] for r in _split(rows, "test")}
    small_keys = {r["key"] for r in _small_test(rows, size=4)}
    assert small_keys <= test_keys


def test_small_test_is_stratified_proportionally() -> None:
    """The subsample keeps both strata in proportion to their test tails
    (5:3 -> 3:1 at size 4 via largest-remainder), never collapsing to one
    stratum. Independent of seed — only WHICH rows are picked is seeded,
    not HOW MANY per stratum."""
    rows = _rows()
    counts: dict[str, int] = {}
    for r in _small_test(rows, size=4):
        counts[r["stratum"]] = counts.get(r["stratum"], 0) + 1
    assert counts == {"a": 3, "b": 1}


def test_small_test_caps_at_test_size() -> None:
    """A target larger than the test tail yields the WHOLE tail (never more,
    never an IndexError) — ``min(size, |test|)``."""
    rows = _rows()
    test_keys = {r["key"] for r in _split(rows, "test")}
    capped = _small_test(rows, size=10_000)
    assert {r["key"] for r in capped} == test_keys


def test_small_test_is_deterministic_under_fixed_seed() -> None:
    """Same seed -> byte-identical subsample (order included)."""
    rows = _rows()
    first = [r["key"] for r in _small_test(rows, size=4, seed=7)]
    second = [r["key"] for r in _small_test(rows, size=4, seed=7)]
    assert first == second
