"""The one-time draw of the chat slice's ``reserved`` records.

``reserved`` is consumed exactly once, by the program's final confirmation, so
its membership must not depend on anything an experiment can touch. It was drawn
ONCE, when the held-out set was authored, and stored as the literal
``metadata.split`` of each record: the loader reads it and never draws. This
module keeps that draw reproducible — the vendored-records test re-runs it over
the stored held-out records and fails when a stored literal disagrees, so a hand
edit to the membership shows up as a failure instead of a silent re-draw.

The draw is stratified by shape. Every shape keeps one reserved seat, so final
confirmation sees every shape; each remaining seat goes to the shape with the
most records still unreserved, ties to the earlier shape name. Inside a shape a
seeded shuffle of the sorted task ids decides which records are reserved, with
one generator shared across shapes taken in sorted order (the ``_split.py``
determinism rule), so the draw does not depend on the order records arrive in.

Example:
    >>> held_out = {"q10": "where", "q11": "where", "q12": "why", "q13": "why"}
    >>> len(draw_reserved(held_out, count=2))
    2
"""

from __future__ import annotations

import random
from collections.abc import Mapping

_RESERVED_COUNT = 10
_RESERVED_DRAW_SEED = 0


def draw_reserved(
    shape_of: Mapping[str, str],
    *,
    count: int = _RESERVED_COUNT,
    seed: int = _RESERVED_DRAW_SEED,
) -> frozenset[str]:
    """The task ids drawn into ``reserved`` from the held-out ``task id → shape`` map.

    Raises:
        ValueError: ``count`` cannot give every shape a seat, or exceeds the records.
    """
    ids_by_shape: dict[str, list[str]] = {}
    for task_id, shape in shape_of.items():
        ids_by_shape.setdefault(shape, []).append(task_id)
    seats = _seats_per_shape({shape: len(ids) for shape, ids in ids_by_shape.items()}, count)
    rng = random.Random(seed)
    reserved: list[str] = []
    for shape in sorted(ids_by_shape):
        ids = sorted(ids_by_shape[shape])
        rng.shuffle(ids)
        reserved.extend(ids[: seats[shape]])
    return frozenset(reserved)


def _seats_per_shape(sizes: Mapping[str, int], count: int) -> dict[str, int]:
    """One seat per shape, then each extra seat where the most records remain unreserved."""
    if not len(sizes) <= count <= sum(sizes.values()):
        raise ValueError(
            f"count = {count}, expected at least one seat per shape ({len(sizes)}) "
            f"and at most the held-out records ({sum(sizes.values())})"
        )
    seats = dict.fromkeys(sizes, 1)
    for _ in range(count - len(sizes)):
        shape = min(sizes, key=lambda name: (seats[name] - sizes[name], name))
        seats[shape] += 1
    return seats


__all__ = ("draw_reserved",)
