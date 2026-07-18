"""DEPRECATED shim — the module moved to ``pydocs_eval.datasets.corpus``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so the old import
keeps working for one deprecation release; the shim is removed in the
release after 0.2.x. Import from ``pydocs_eval.datasets.corpus`` instead.
"""

from __future__ import annotations

from .datasets.corpus import materialize_corpus  # noqa: F401
