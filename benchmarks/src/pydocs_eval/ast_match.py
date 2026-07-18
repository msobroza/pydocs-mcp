"""DEPRECATED shim — the module moved to ``pydocs_eval.metrics.ast_match``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so the old import
keeps working for one deprecation release; the shim is removed in the
release after 0.2.x. Import from ``pydocs_eval.metrics.ast_match`` instead.
"""

from __future__ import annotations

from .metrics.ast_match import (  # noqa: F401
    ast_equivalent,
    find_first_match_rank,
)
