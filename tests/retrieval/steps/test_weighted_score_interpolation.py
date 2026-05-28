"""AC-4 + AC-5: WeightedScoreInterpolationStep blends per-branch scores."""

from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.weighted_score_interpolation import (
    WeightedScoreInterpolationStep,
)


def _q(terms: str = "x") -> SearchQuery:
    return SearchQuery(terms=terms, max_results=10)


def _chunk(cid: int, score: float, text: str = "") -> Chunk:
    """Helper to build a scored chunk."""
    return Chunk(text=text or f"chunk-{cid}", id=cid, relevance=score)


def _ranked(items: list[Chunk]) -> ChunkList:
    return ChunkList(items=tuple(items))


@pytest.mark.asyncio
async def test_equal_weights_blend_min_max_normalized_scores() -> None:
    """BM25 scores in [0, 10], dense in [0, 1]. After min-max norm,
    equal-weighted blend produces (norm_bm25 + norm_dense) / 2."""
    state = RetrieverState(
        query=_q(),
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked": _ranked([_chunk(1, 10.0), _chunk(2, 5.0)]),
            "dense.ranked": _ranked([_chunk(1, 1.0), _chunk(2, 0.5)]),
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.5, 0.5),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    assert out.candidates is not None
    items = list(out.candidates.items)
    by_id = {c.id: c.relevance for c in items}
    assert by_id[1] == pytest.approx(1.0)
    assert by_id[2] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_asymmetric_weights() -> None:
    state = RetrieverState(
        query=_q(),
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked": _ranked([_chunk(1, 10.0), _chunk(2, 0.0)]),
            "dense.ranked": _ranked([_chunk(1, 0.0), _chunk(2, 1.0)]),
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.8, 0.2),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    by_id = {c.id: c.relevance for c in out.candidates.items}
    assert by_id[1] == pytest.approx(0.8)
    assert by_id[2] == pytest.approx(0.2)


def test_from_dict_validates_weights_sum() -> None:
    """Weights that don't sum to ~1.0 raise in from_dict."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    with pytest.raises(ValueError, match="sum"):
        WeightedScoreInterpolationStep.from_dict(
            {
                "type": "weighted_score_interpolation",
                "weights": [0.3, 0.3],
                "branch_keys": ["a", "b"],
            },
            BuildContext(),
        )


def test_round_trip_yaml() -> None:
    """to_dict / from_dict round-trips structural equality."""
    from pydocs_mcp.retrieval.serialization import BuildContext

    original = WeightedScoreInterpolationStep(
        weights=(0.6, 0.4),
        branch_keys=("a", "b"),
        name="custom_name",
    )
    rebuilt = WeightedScoreInterpolationStep.from_dict(
        original.to_dict(),
        BuildContext(),
    )
    assert rebuilt.weights == original.weights
    assert rebuilt.branch_keys == original.branch_keys
    assert rebuilt.name == original.name


@pytest.mark.asyncio
async def test_missing_branch_key_raises_diagnostic() -> None:
    """A declared branch_key MUST be present in state.scratch; missing
    keys raise KeyError with a diagnostic listing the missing key + the
    available scratch keys.

    Louder than RRFFusionStep on purpose: a missing branch usually
    means an upstream pipeline misconfiguration (e.g., TopKFilterStep
    forgot to publish_to a matching name). Silent skip would hide the
    bug behind worse retrieval quality.
    """
    state = RetrieverState(
        query=_q(),
        candidates=None,
        result=None,
        scratch={
            "bm25.ranked": _ranked([_chunk(1, 10.0)]),
            # "dense.ranked" deliberately absent
        },
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.5, 0.5),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    with pytest.raises(KeyError) as exc_info:
        await step.run(state)
    # Diagnostic mentions the missing key + the available keys.
    message = str(exc_info.value)
    assert "dense.ranked" in message
    assert "bm25.ranked" in message  # listed as available


@pytest.mark.asyncio
async def test_multiple_missing_keys_all_listed() -> None:
    """When several branch_keys are absent, the diagnostic lists all of
    them — not just the first one — so the user sees the full gap."""
    state = RetrieverState(
        query=_q(),
        candidates=None,
        result=None,
        scratch={},  # both branches absent
    )
    step = WeightedScoreInterpolationStep(
        weights=(0.5, 0.5),
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    with pytest.raises(KeyError) as exc_info:
        await step.run(state)
    message = str(exc_info.value)
    assert "bm25.ranked" in message
    assert "dense.ranked" in message
