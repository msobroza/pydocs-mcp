"""In-memory inventory tracking per-SKU quantities and unit prices."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Inventory:
    """Track a quantity and a unit price for each SKU."""

    _quantities: dict[str, int] = field(default_factory=dict)
    _prices: dict[str, float] = field(default_factory=dict)

    def add_item(self, sku: str, quantity: int, unit_price: float) -> None:
        """Add ``quantity`` units of ``sku`` at ``unit_price`` each."""
        if quantity < 0:
            raise ValueError(f"quantity must be non-negative, got {quantity!r} for {sku!r}")
        self._quantities[sku] = self._quantities.get(sku, 0) + quantity
        self._prices[sku] = unit_price

    def quantity_of(self, sku: str) -> int:
        """Return the current quantity of ``sku`` (0 if unknown)."""
        return self._quantities.get(sku, 0)

    def total_value(self) -> float:
        """Return the total monetary value of all stock (Σ quantity × unit_price)."""
        return float(sum(self._quantities.values()))
