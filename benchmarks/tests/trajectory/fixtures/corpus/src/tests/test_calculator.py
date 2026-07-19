"""Tests for widgetlib.calculator."""

import pytest

from widgetlib.calculator import add, average, multiply


def test_add():
    assert add(2, 3) == 5


def test_multiply():
    assert multiply(4, 5) == 20


def test_average_of_three():
    assert average([1, 2, 3]) == 2.0


def test_average_single():
    assert average([5]) == 5.0


def test_average_empty_raises():
    with pytest.raises(ValueError):
        average([])
