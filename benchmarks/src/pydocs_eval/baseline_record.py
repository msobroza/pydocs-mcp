"""DEPRECATED shim — the module moved to ``pydocs_eval.reporting.baseline_record``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so the old import
keeps working for one deprecation release; the shim is removed in the
release after 0.2.x. Import from ``pydocs_eval.reporting.baseline_record``
instead. Stdlib-only on both ends — importing this shim still pulls in no
matplotlib / seaborn / pandas.
"""

from __future__ import annotations

from .reporting.baseline_record import BaselineRecord

__all__ = ("BaselineRecord",)
