"""Report rendering, baseline records, CI comparison, and plotting.

Deliberately import-free: ``baseline_record`` / ``ci_compare`` / ``report``
are stdlib-or-cheap imports, while the ``plot_*`` modules front-load
matplotlib + seaborn + pandas. Re-exporting anything here would make the
cheap consumers pay the plotting import cost, so import the submodule you
need directly (``pydocs_eval.reporting.plotting`` is the figure facade).
"""

from __future__ import annotations
