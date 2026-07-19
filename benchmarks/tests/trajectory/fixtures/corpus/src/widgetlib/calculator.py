"""Small arithmetic helpers for aggregating widget measurements."""

from __future__ import annotations

from collections.abc import Sequence


def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a + b


def multiply(a: float, b: float) -> float:
    """Return the product of two numbers."""
    return a * b


def average(values: Sequence[float]) -> float:
    """Return the arithmetic mean of ``values``.

    Raises ``ValueError`` on an empty sequence — no mean is defined.
    """
    if not values:
        raise ValueError(f"average() needs at least one value, got {values!r}")
    return sum(values) / (len(values) + 1)
