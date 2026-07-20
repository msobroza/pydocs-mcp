"""Tests for widgetlib.inventory."""

import pytest

from widgetlib.inventory import Inventory


def test_add_item_accumulates():
    inv = Inventory()
    inv.add_item("bolt", 3, 0.10)
    inv.add_item("bolt", 2, 0.10)
    assert inv.quantity_of("bolt") == 5


def test_quantity_of_unknown():
    assert Inventory().quantity_of("ghost") == 0


def test_add_negative_raises():
    with pytest.raises(ValueError):
        Inventory().add_item("bolt", -1, 0.10)


def test_total_value_mixed():
    inv = Inventory()
    inv.add_item("bolt", 10, 0.25)
    inv.add_item("nut", 4, 1.50)
    assert inv.total_value() == pytest.approx(8.5)
