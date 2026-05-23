"""Pipeline class with self-attribute calls. Exercises Rule 0 (self.X.Y rewrite)
+ Rule 5 (self-method short-circuit)."""
from __future__ import annotations

from dataclasses import dataclass

from ac15_pkg.types_and_helpers import compute_sum, compute_product


@dataclass(frozen=True)
class Pipeline:
    """Test class for self-attribute resolution."""

    multiplier: int = 1

    def process(self, a: int, b: int) -> int:
        """Calls compute_sum (cross-module, Rule B) and compute_product
        (also Rule B); calls self.scale (Rule 5: self-method short-circuit)."""
        intermediate = compute_sum(a, b)
        return self.scale(compute_product(a, b)) + intermediate

    def scale(self, value: int) -> int:
        return value * self.multiplier
