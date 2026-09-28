"""Holm's step-down adjustment, pinned on textbook vectors (``p.adjust(p, "holm")``).

The compare verb adjusts each paired test's p across at most three variants
against one baseline, so a family of one is left alone and a family of three
never reads more significant than its smallest raw p allows.
"""

from __future__ import annotations

import math

import pytest

from pydocs_eval.metrics.aggregate import holm_adjust


@pytest.mark.parametrize(
    ("raw", "adjusted"),
    [
        ((0.01, 0.02, 0.03, 0.04, 0.05), (0.05, 0.08, 0.09, 0.09, 0.09)),
        ((0.01, 0.04, 0.03), (0.03, 0.06, 0.06)),
        ((0.04, 0.01, 0.03), (0.06, 0.03, 0.06)),
        ((0.5, 0.6), (1.0, 1.0)),
        ((0.02,), (0.02,)),
        ((), ()),
    ],
)
def test_holm_matches_the_textbook_step_down(
    raw: tuple[float, ...], adjusted: tuple[float, ...]
) -> None:
    assert holm_adjust(raw) == pytest.approx(adjusted)


def test_holm_never_reorders_the_family() -> None:
    raw = (0.03, 0.001, 0.2, 0.015)

    held = holm_adjust(raw)

    by_raw = sorted(range(len(raw)), key=lambda index: raw[index])
    assert [held[index] for index in by_raw] == sorted(held)
    assert all(new >= old for new, old in zip(held, raw, strict=True))


@pytest.mark.parametrize("bad", [-0.1, 1.5, math.nan])
def test_a_p_value_outside_the_unit_interval_is_refused_by_value(bad: float) -> None:
    with pytest.raises(ValueError, match="expected a p-value in"):
        holm_adjust((0.01, bad))
