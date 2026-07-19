"""Tests for widgetlib.pricing."""

import pytest

from widgetlib.pricing import apply_discount, with_tax


def test_apply_discount_quarter():
    assert apply_discount(100.0, 0.25) == pytest.approx(75.0)


def test_apply_discount_zero_is_noop():
    assert apply_discount(100.0, 0.0) == pytest.approx(100.0)


def test_apply_discount_rejects_out_of_range():
    with pytest.raises(ValueError):
        apply_discount(100.0, 1.5)


def test_with_tax():
    assert with_tax(100.0, 0.2) == pytest.approx(120.0)


def test_with_tax_rejects_negative():
    with pytest.raises(ValueError):
        with_tax(100.0, -0.1)
