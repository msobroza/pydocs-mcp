"""DEPRECATED shim — the module moved to ``pydocs_eval.reporting.report``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so the old import
keeps working for one deprecation release; the shim is removed in the
release after 0.2.x. Import from ``pydocs_eval.reporting.report`` instead.
"""

from __future__ import annotations

from .reporting.report import (  # noqa: F401
    TaskRow,
    TaskRows,
    format_report,
)
