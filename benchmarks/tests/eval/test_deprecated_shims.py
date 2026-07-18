"""The 0.2.x deprecation shims: every pre-restructure module path still
imports and re-exports the SAME objects as its canonical new home.

pydocs-mcp-eval 0.2.0 published the flat package-root paths on PyPI, so each
moved module keeps a one-release shim. These tests pin the shim contract —
old path importable, identity (not a copy) with the canonical object — so a
shim can't silently rot before its scheduled removal. Everything else in the
suite imports the canonical paths; these two tests are the only place the
old paths appear on purpose.
"""

from __future__ import annotations

import importlib

import pytest

# (old module path, canonical module path, re-exported public names)
_SHIMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "pydocs_eval.ast_match",
        "pydocs_eval.metrics.ast_match",
        ("ast_equivalent", "find_first_match_rank"),
    ),
    ("pydocs_eval.corpus", "pydocs_eval.datasets.corpus", ("materialize_corpus",)),
    ("pydocs_eval.report", "pydocs_eval.reporting.report", ("format_report",)),
    ("pydocs_eval.baseline_record", "pydocs_eval.reporting.baseline_record", ("BaselineRecord",)),
    ("pydocs_eval.ci_compare", "pydocs_eval.reporting.ci_compare", ("main",)),
    (
        "pydocs_eval.serialization",
        "pydocs_eval.registries",
        ("dataset_registry", "metric_registry", "system_registry", "tracker_registry"),
    ),
)


@pytest.mark.parametrize(("old", "new", "names"), _SHIMS, ids=[s[0] for s in _SHIMS])
def test_shim_reexports_canonical_objects(old: str, new: str, names: tuple[str, ...]) -> None:
    old_mod = importlib.import_module(old)
    new_mod = importlib.import_module(new)
    for name in names:
        assert getattr(old_mod, name) is getattr(new_mod, name), (
            f"{old}.{name} is not the canonical {new}.{name}"
        )


def test_plotting_shim_keeps_public_surface_and_cli() -> None:
    """``pydocs_eval.plotting`` re-exports the figure functions AND the CLI
    entry (``python -m pydocs_eval.plotting`` routes through ``main``)."""
    import pydocs_eval.plotting as old
    import pydocs_eval.reporting.plotting as new

    for name in ("BaselineRecord", "plot_baselines", "plot_metric_vs_latency", "plot_timings"):
        assert getattr(old, name) is getattr(new, name)
    assert old.main is new.main
    assert old._cli_main is new._cli_main
