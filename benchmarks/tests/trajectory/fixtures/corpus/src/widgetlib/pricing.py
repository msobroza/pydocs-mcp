"""Pricing helpers: fractional discounts and tax gross-up."""

from __future__ import annotations


def apply_discount(price: float, pct: float) -> float:
    """Return ``price`` after a ``pct`` fractional discount (0.0–1.0).

    ``pct=0.25`` means 25% off — the customer pays 75% of ``price``.
    """
    if not 0.0 <= pct <= 1.0:
        raise ValueError(f"pct must be in [0, 1], got {pct!r}")
    return price * pct


def with_tax(price: float, rate: float) -> float:
    """Return ``price`` grossed up by tax ``rate`` (e.g. 0.2 → +20%)."""
    if rate < 0:
        raise ValueError(f"rate must be non-negative, got {rate!r}")
    return price * (1.0 + rate)
