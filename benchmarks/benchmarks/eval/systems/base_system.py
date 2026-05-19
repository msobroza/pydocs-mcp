"""Re-export the ``System`` Protocol so concrete systems import from one
place and never reach across into ``..protocols`` from sibling modules.
"""
from __future__ import annotations

from ..protocols import System

__all__ = ["System"]
