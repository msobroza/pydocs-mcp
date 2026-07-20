"""widgetlib — a tiny fixture package for trajectory-attribution testing.

Deliberately small (four single-responsibility modules) so a planted bug lives
in exactly one file with an unambiguous gold fix. Used by the Phase 2
trajectory fixtures (ADR 0011): a rollout localizes and fixes one planted bug,
and the merged trace is hand-labeled against the attributor.
"""

from widgetlib.calculator import add, average, multiply
from widgetlib.inventory import Inventory
from widgetlib.pricing import apply_discount, with_tax
from widgetlib.textutil import slugify, truncate

__all__ = [
    "Inventory",
    "add",
    "apply_discount",
    "average",
    "multiply",
    "slugify",
    "truncate",
    "with_tax",
]
