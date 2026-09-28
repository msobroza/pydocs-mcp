"""The one-time ``reserved`` draw of the chat slice, stratified by shape.

The held-out records' ``reserved`` membership is drawn once, at authoring time,
and stored as literals; the loader never draws. These tests pin the draw's rule
so the vendored records can be checked against it.
"""

from __future__ import annotations

from collections import Counter

import pytest

from pydocs_eval.datasets.example_needle_chat_reserved import draw_reserved


def _held_out(sizes: dict[str, int]) -> dict[str, str]:
    """Task id → shape, ``sizes[shape]`` records per shape."""
    return {f"{shape}-{i}": shape for shape, n in sizes.items() for i in range(n)}


def _seats(reserved: frozenset[str], held_out: dict[str, str]) -> Counter[str]:
    return Counter(held_out[task_id] for task_id in reserved)


def test_every_shape_keeps_one_seat_and_the_rest_go_where_most_remain() -> None:
    held_out = _held_out({"compare": 2, "where": 2, "where_all": 6})

    reserved = draw_reserved(held_out, count=5, seed=0)

    # One seat each, then both extra seats to where_all (5 then 4 still unreserved).
    assert _seats(reserved, held_out) == {"compare": 1, "where": 1, "where_all": 3}


def test_a_tie_for_an_extra_seat_goes_to_the_earlier_shape_name() -> None:
    held_out = _held_out({"how_flow": 3, "where_all": 3, "why": 1})

    reserved = draw_reserved(held_out, count=4, seed=0)

    assert _seats(reserved, held_out) == {"how_flow": 2, "where_all": 1, "why": 1}


def test_the_draw_is_a_subset_of_the_held_out_records_of_the_asked_size() -> None:
    held_out = _held_out({"compare": 4, "how_flow": 5, "how_to": 4, "where": 3, "why": 4})

    reserved = draw_reserved(held_out, count=8, seed=0)

    assert len(reserved) == 8
    assert reserved <= set(held_out)


def test_the_same_records_and_seed_draw_the_same_members_in_any_order() -> None:
    held_out = _held_out({"compare": 4, "how_flow": 5, "vague": 4, "where": 3})
    reversed_order = dict(reversed(list(held_out.items())))

    first = draw_reserved(held_out, count=6, seed=0)

    assert draw_reserved(reversed_order, count=6, seed=0) == first
    assert draw_reserved(held_out, count=6, seed=0) == first


def test_the_seed_decides_which_records_of_a_shape_are_drawn() -> None:
    held_out = _held_out({"where_all": 12})

    draws = {draw_reserved(held_out, count=3, seed=seed) for seed in range(5)}

    assert len(draws) > 1


@pytest.mark.parametrize("count", [1, 11])
def test_a_count_that_cannot_seat_every_shape_or_exceeds_the_records_is_refused(
    count: int,
) -> None:
    held_out = _held_out({"compare": 5, "where": 3, "why": 2})

    with pytest.raises(ValueError, match=f"count = {count}"):
        draw_reserved(held_out, count=count, seed=0)
