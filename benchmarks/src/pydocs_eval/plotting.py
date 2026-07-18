"""DEPRECATED shim — the module moved to ``pydocs_eval.reporting.plotting``.

pydocs-mcp-eval 0.2.0 published this module path on PyPI, so both the old
import and ``python -m pydocs_eval.plotting`` keep working for one
deprecation release; the shim is removed in the release after 0.2.x. Use
``python -m pydocs_eval.reporting.plotting`` (or the ``pydocs-eval-plot``
console command), and import the figure functions from
``pydocs_eval.reporting.plotting`` (facade) or their per-figure homes
(``reporting/plot_baselines.py`` etc.) instead.
"""

from __future__ import annotations

from .reporting.plotting import (  # noqa: F401
    BaselineRecord,
    _cli_main,
    main,
    plot_baselines,
    plot_metric_vs_latency,
    plot_timings,
)

__all__ = (
    "BaselineRecord",
    "plot_baselines",
    "plot_metric_vs_latency",
    "plot_timings",
)

if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    raise SystemExit(main())
