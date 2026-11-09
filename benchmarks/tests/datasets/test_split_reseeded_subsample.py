"""``reseeded_stratified_subsample``: a fixed-size stratified redraw under its own seed.

The fresh repoqa test draw keeps the dev/test partition where the dataset's own
``split_seed`` put it and redraws only the subsample, so this helper takes the
partition's rows as given: it re-sorts each stratum, shuffles it with ONE
``random.Random(seed)`` and apportions the size exactly as ``small_test`` does
(Hamilton's largest remainder).
"""

from __future__ import annotations

from collections import Counter

from pydocs_eval.datasets._split import reseeded_stratified_subsample


def _rows(sizes: dict[str, int]) -> list[dict[str, str]]:
    return [
        {"stratum": stratum, "key": f"{stratum}{i:02d}"}
        for stratum, size in sizes.items()
        for i in range(size)
    ]


def _draw(rows: list[dict[str, str]], *, seed: int, size: int) -> list[dict[str, str]]:
    return reseeded_stratified_subsample(
        rows,
        seed=seed,
        size=size,
        stratum_of=lambda row: row["stratum"],
        sort_key=lambda row: row["key"],
    )


def test_the_redraw_has_the_requested_size_apportioned_by_stratum() -> None:
    drawn = _draw(_rows({"a": 5, "b": 5, "c": 5}), seed=1, size=9)

    assert Counter(row["stratum"] for row in drawn) == {"a": 3, "b": 3, "c": 3}


def test_leftover_seats_go_to_the_largest_remainders() -> None:
    # quotas 4 * 6/10 = 2.4 and 4 * 4/10 = 1.6: floors 2 + 1, the seat goes to b.
    drawn = _draw(_rows({"a": 6, "b": 4}), seed=0, size=4)

    assert Counter(row["stratum"] for row in drawn) == {"a": 2, "b": 2}


def test_the_redraw_is_capped_at_the_rows_it_is_given() -> None:
    assert len(_draw(_rows({"a": 2, "b": 1}), seed=3, size=30)) == 3


def test_the_redraw_ignores_the_order_rows_arrive_in() -> None:
    rows = _rows({"a": 8, "b": 8})

    assert _draw(rows, seed=1, size=6) == _draw(list(reversed(rows)), seed=1, size=6)


def test_another_seed_draws_other_rows() -> None:
    rows = _rows({"a": 10, "b": 10})

    assert _draw(rows, seed=1, size=6) != _draw(rows, seed=2, size=6)


def test_an_empty_pool_draws_nothing() -> None:
    assert _draw([], seed=1, size=30) == []
