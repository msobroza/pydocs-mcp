"""DEPRECATED shim — the module moved to ``pydocs_eval.reporting.ci_compare``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so both the old
import and ``python -m pydocs_eval.ci_compare`` keep working for one
deprecation release; the shim is removed in the release after 0.2.x. Use
``python -m pydocs_eval.reporting.ci_compare`` (or the
``pydocs-eval-ci-compare`` console command) instead.
"""

from __future__ import annotations

from .reporting.ci_compare import main

if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    raise SystemExit(main())
