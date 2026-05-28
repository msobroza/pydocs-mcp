"""Shared step constants.

Kept in a separate module so updates are a one-line change and don't
churn the step files that consume them. Per
CLAUDE.md §"Default values: single source of truth".

- :data:`DEFAULT_BRANCH_KEYS` — the canonical pair of scratch-key names
  the shipped hybrid pipeline publishes branch rankings under
  (``bm25.ranked`` + ``dense.ranked``). Used as the field default on
  :class:`~pydocs_mcp.retrieval.steps.rrf_fusion.RRFFusionStep` and
  :class:`~pydocs_mcp.retrieval.steps.weighted_score_interpolation.WeightedScoreInterpolationStep`.
- :data:`PRE_FILTER_SCRATCH_KEY` — the scratch key
  :class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterStep`
  publishes its typed
  :class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterResult` under,
  and the same key the downstream
  :class:`~pydocs_mcp.retrieval.steps.chunk_fetcher.ChunkFetcherStep` /
  :class:`~pydocs_mcp.retrieval.steps.member_fetcher.MemberFetcherStep` /
  :class:`~pydocs_mcp.retrieval.steps.dense_fetcher.DenseFetcherStep`
  read from. Re-exported from
  :mod:`pydocs_mcp.retrieval.steps.pre_filter` so existing fetcher
  imports keep working without modification.
"""

from __future__ import annotations

DEFAULT_BRANCH_KEYS: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
PRE_FILTER_SCRATCH_KEY: str = "pre_filter.result"

__all__ = ("DEFAULT_BRANCH_KEYS", "PRE_FILTER_SCRATCH_KEY")
