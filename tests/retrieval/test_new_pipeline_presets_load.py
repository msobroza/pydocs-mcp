"""All four new YAML presets parse + assemble without error (AC-21 prereq).

Validates schema-level contract of the four new chunk-search presets:

- ``chunk_search_dense.yaml`` — dense-only composite (with token_budget_formatter)
- ``chunk_search_dense_ranked.yaml`` — dense-only ranked (no formatter, for benchmarks)
- ``chunk_search_hybrid.yaml`` — BM25 + Dense fused via RRF, composite
- ``chunk_search_hybrid_ranked.yaml`` — BM25 + Dense fused via RRF, ranked

Pure YAML parsing — no pipeline instantiation. Building any of these
preset would require :class:`BuildContext` with a non-None ``embedder``
and ``vector_store`` (see DenseFetcherStep.from_dict + DenseScorerStep.from_dict);
tests that exercise the build path live alongside the dense steps' own
test modules. These tests only assert "the YAML the project ships parses
and has the right shape".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PIPELINES_DIR = Path(__file__).resolve().parents[2] / "python" / "pydocs_mcp" / "pipelines"

# Mirrors RRFFusionStep's field default (rrf_fusion.py) and
# WeightedScoreInterpolationStep's — the scratch-key contract check below
# needs the SAME fallback the runtime uses when a preset's rrf_fusion step
# omits branch_keys (params: {}). Single literal, not re-derived from an
# import, so this test stays a pure-YAML structural check (module docstring
# promise) with no BuildContext / package import required.
_DEFAULT_BRANCH_KEYS = ("bm25.ranked", "dense.ranked")

# Steps whose params param name publishes a Chunk list into state.scratch
# under a caller-chosen key, keyed by the YAML `type`. rrf_fusion's own
# `branch_keys` param is intentionally excluded — it is a CONSUMER of
# scratch keys, not a producer.
_PUBLISH_PARAM_BY_STEP_TYPE = {
    "top_k_filter": "publish_to",
    "dense_scorer": "publish_to",
    "late_interaction_scorer": "publish_to",
    "weighted_score_interpolation": "publish_to",
    # llm_tree_reasoning.py: _DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked" —
    # the field default when a preset (e.g. tree_only.yaml) never sets it.
    "llm_tree_reasoning": "output_scratch_key",
}
_DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked"


def _published_keys(steps: list[dict[str, Any]]) -> set[str]:
    """Collect every scratch key this (possibly nested) step list publishes.

    Recurses into ``parallel_retrieval`` branches and ``conditional``'s
    ``stage`` so a key published inside a nested scope (e.g. the inner
    hybrid sub-pipeline in chunk_search_with_tree_reasoning_parallel.yaml)
    is visible to an rrf_fusion step that reads it from an outer scope.
    """
    keys: set[str] = set()
    for step in steps:
        step_type = step.get("type")
        params = step.get("params") or {}
        publish_param = _PUBLISH_PARAM_BY_STEP_TYPE.get(step_type)
        if publish_param is not None:
            value = params.get(publish_param)
            if value is not None:
                keys.add(value)
            elif step_type == "llm_tree_reasoning":
                keys.add(_DEFAULT_OUTPUT_SCRATCH_KEY)
        if step_type == "parallel_retrieval":
            for branch in params.get("branches", []):
                keys |= _published_keys(branch.get("steps", []))
        if step_type == "conditional":
            stage = params.get("stage")
            if stage is not None:
                keys |= _published_keys([stage])
    return keys


def _iter_rrf_fusion_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Depth-first collection of every rrf_fusion step, nested or not."""
    found: list[dict[str, Any]] = []
    for step in steps:
        step_type = step.get("type")
        params = step.get("params") or {}
        if step_type == "rrf_fusion":
            found.append(step)
        if step_type == "parallel_retrieval":
            for branch in params.get("branches", []):
                found.extend(_iter_rrf_fusion_steps(branch.get("steps", [])))
        if step_type == "conditional":
            stage = params.get("stage")
            if stage is not None:
                found.extend(_iter_rrf_fusion_steps([stage]))
    return found


ALL_PRESETS_WITH_RRF_FUSION = (
    "chunk_search_deps.yaml",
    "chunk_search_hybrid.yaml",
    "chunk_search_hybrid_ranked.yaml",
    "chunk_search_late_interaction.yaml",
    "chunk_search_late_interaction_ranked.yaml",
    "chunk_search_with_tree_reasoning_after.yaml",
    "chunk_search_with_tree_reasoning_parallel.yaml",
    "decision_search.yaml",
    "tree_only.yaml",
)


@pytest.mark.parametrize("preset", ALL_PRESETS_WITH_RRF_FUSION)
def test_rrf_fusion_branch_keys_match_published_scratch_keys(preset: str) -> None:
    """Every rrf_fusion step's effective branch_keys must be a subset of the
    scratch keys actually published (via publish_to / output_scratch_key)
    earlier in the pipeline.

    Regression for the scratch-key contract gap: TopKFilterStep.publish_to
    (producer) and RRFFusionStep.branch_keys (consumer) are two independent
    YAML edits with no shared symbol enforcing they agree. rrf_fusion is
    deliberately LENIENT on a missing branch (state.scratch.get(key) ->
    continue, per rrf_fusion.py's module docstring) so a drifted key doesn't
    raise — it silently drops that entire ranked list from the fusion,
    degrading hybrid retrieval to single-list (or, if ALL keys drift,
    zero-list / pipeline-unchanged) fusion with every step-shape test green.

    chunk_search_late_interaction.yaml is the concrete live case this
    catches: its late branch publishes to "late.ranked" via
    late_interaction_scorer's publish_to, but the fuse step ships with
    `params: {}` -- which falls back to RRFFusionStep's DEFAULT_BRANCH_KEYS
    ("bm25.ranked", "dense.ranked"), so "late.ranked" is never read and the
    late-interaction signal is silently dropped from the fused ranking.
    """
    cfg = yaml.safe_load((PIPELINES_DIR / preset).read_text(encoding="utf-8"))
    steps = cfg["steps"]
    published = _published_keys(steps)

    for fusion_step in _iter_rrf_fusion_steps(steps):
        params = fusion_step.get("params") or {}
        branch_keys = (
            tuple(params["branch_keys"]) if "branch_keys" in params else _DEFAULT_BRANCH_KEYS
        )
        missing = [key for key in branch_keys if key not in published]
        assert not missing, (
            f"{preset}: rrf_fusion step {fusion_step.get('name')!r} reads "
            f"branch_keys={branch_keys!r} but scratch key(s) {missing!r} are "
            f"never published (publish_to / output_scratch_key) anywhere "
            f"upstream in this preset — published keys were {sorted(published)!r}. "
            "RRF will silently degrade to fusing only the branches whose "
            "keys DO match; fix the branch_keys param or the publish_to "
            "value so they agree."
        )


NEW_PRESETS = (
    "chunk_search_dense.yaml",
    "chunk_search_dense_ranked.yaml",
    "chunk_search_hybrid.yaml",
    "chunk_search_hybrid_ranked.yaml",
    "chunk_search_graph.yaml",
    "chunk_search_graph_ranked.yaml",
)


@pytest.mark.parametrize("preset", NEW_PRESETS)
def test_preset_file_exists_and_parses(preset: str) -> None:
    path = PIPELINES_DIR / preset
    assert path.exists(), f"missing preset {preset}"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "name" in cfg
    assert "steps" in cfg
    assert len(cfg["steps"]) >= 2


def test_hybrid_preset_has_parallel_then_rrf_fusion() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_hybrid.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "parallel_retrieval" in step_types
    assert "rrf_fusion" in step_types
    assert step_types.index("parallel_retrieval") < step_types.index("rrf_fusion")


def test_dense_preset_uses_dense_fetcher_without_redundant_dense_scorer() -> None:
    """chunk_search_dense.yaml is a single-branch pipeline: dense_fetcher's
    ANN index score IS the turbovec score for that candidate set, so a
    dense_scorer re-score would be pure redundancy. dense_scorer's job is
    POST-FUSION re-ranking of a merged BM25+dense set — see the hybrid
    preset assertion below."""
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_dense.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "dense_fetcher" in step_types
    assert "dense_scorer" not in step_types


def test_hybrid_preset_runs_dense_scorer_after_rrf_fusion() -> None:
    """dense_scorer is the POST-FUSION re-ranker: it must sit after
    rrf_fusion (which produces the merged BM25+dense candidate set) and
    before the final limit, not inside the dense branch pre-fusion."""
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_hybrid.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "dense_scorer" in step_types
    assert (
        step_types.index("rrf_fusion")
        < step_types.index("dense_scorer")
        < step_types.index("limit")
    )


def test_ranked_variants_drop_token_budget_formatter() -> None:
    for ranked in (
        "chunk_search_dense_ranked.yaml",
        "chunk_search_hybrid_ranked.yaml",
        "chunk_search_graph_ranked.yaml",
    ):
        cfg = yaml.safe_load((PIPELINES_DIR / ranked).read_text(encoding="utf-8"))
        step_types = [s["type"] for s in cfg["steps"]]
        assert "token_budget_formatter" not in step_types


def test_graph_preset_runs_graph_expand_after_filter() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_graph.yaml").read_text(encoding="utf-8"),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    # No dense_scorer: single-branch dense pipeline, same rationale as
    # chunk_search_dense.yaml (dense_fetcher's ANN score IS the turbovec
    # score for this candidate set).
    assert {"dense_fetcher", "graph_expand"} <= set(step_types)
    assert "dense_scorer" not in step_types
    # graph_expand sits after the metadata filter, before the top-k cutoff.
    assert (
        step_types.index("metadata_post_filter")
        < step_types.index("graph_expand")
        < step_types.index("top_k_filter")
    )
