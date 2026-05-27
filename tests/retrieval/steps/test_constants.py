"""Tests for shared step constants in ``retrieval/steps/_constants.py``.

The constants live in a dedicated module so that updates are a one-line
change and don't churn unrelated step files. This module pins:

- ``DEFAULT_BRANCH_KEYS`` — the canonical pair of scratch-key names the
  hybrid pipeline publishes branch rankings under
  (``bm25.ranked`` + ``dense.ranked``). Used as the field default on
  :class:`RRFFusionStep` and :class:`WeightedScoreInterpolationStep`.
- ``PRE_FILTER_SCRATCH_KEY`` — the canonical scratch key
  :class:`PreFilterStep` publishes the typed :class:`PreFilterResult`
  under. Re-exported from ``pre_filter.py`` for backward compatibility
  with existing fetcher imports.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.steps import (
    pre_filter,
    rrf_fusion,
    weighted_score_interpolation,
)
from pydocs_mcp.retrieval.steps._constants import (
    DEFAULT_BRANCH_KEYS,
    PRE_FILTER_SCRATCH_KEY,
)


def test_default_branch_keys_shape() -> None:
    """Sanity-check the canonical default — order matters for downstream
    fusers that index ``weights`` in the same order as ``branch_keys``.
    """
    assert DEFAULT_BRANCH_KEYS == ("bm25.ranked", "dense.ranked")
    assert isinstance(DEFAULT_BRANCH_KEYS, tuple)


def test_pre_filter_scratch_key_value() -> None:
    """The scratch key is the public contract between
    :class:`PreFilterStep` and the downstream fetchers. Pin its value.
    """
    assert PRE_FILTER_SCRATCH_KEY == "pre_filter.result"


def test_rrf_uses_shared_constant() -> None:
    """:class:`RRFFusionStep` reads its ``branch_keys`` default from the
    shared constant — no inline literal duplicated in the step module.
    """
    assert rrf_fusion.RRFFusionStep().branch_keys == DEFAULT_BRANCH_KEYS


def test_weighted_uses_shared_constant() -> None:
    """:class:`WeightedScoreInterpolationStep` reads its ``branch_keys``
    default from the shared constant — no inline literal duplicated.
    """
    step = weighted_score_interpolation.WeightedScoreInterpolationStep()
    assert step.branch_keys == DEFAULT_BRANCH_KEYS


def test_pre_filter_re_exports_shared_constant() -> None:
    """``pre_filter.py`` re-exports ``PRE_FILTER_SCRATCH_KEY`` so the
    existing fetcher imports
    (``from pydocs_mcp.retrieval.steps.pre_filter import PRE_FILTER_SCRATCH_KEY``)
    keep working after the constant moved to ``_constants.py``.
    """
    assert pre_filter.PRE_FILTER_SCRATCH_KEY is PRE_FILTER_SCRATCH_KEY
