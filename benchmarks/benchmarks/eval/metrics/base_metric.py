"""Re-export the ``Metric`` Protocol so concrete metrics import from one
place and never reach across into ``..protocols`` from sibling modules."""
from __future__ import annotations

from ..protocols import Metric

__all__ = ["Metric"]
