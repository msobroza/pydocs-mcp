"""Re-export the ``Dataset`` Protocol so concrete datasets import from one
place and never reach across into ``..protocols`` from sibling modules.
"""
from __future__ import annotations

from ..protocols import Dataset

__all__ = ["Dataset"]
