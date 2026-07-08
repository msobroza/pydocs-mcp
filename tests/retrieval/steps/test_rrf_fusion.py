"""RRFFusionStep multi-list fusion + RRFResultFuser math (AC-19 + spec §5.6)."""

from __future__ import annotations

import pytest

from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.rrf_fusion import RRFFusionStep, RRFResultFuser


def _q(terms: str = "x") -> SearchQuery:
    return SearchQuery(terms=terms, max_results=10)


@pytest.mark.asyncio
async def test_rrf_formula_correct() -> None:
    """Hand-computed RRF: score = sum(1 / (k + rank)) across lists.

    With k=60 and two lists [a,b] / [b,a]:
      score(a) = 1/60 + 1/61
      score(b) = 1/61 + 1/60
    Scores are equal; both items appear in fused output.
    """
    fuser = RRFResultFuser(k=60)
    a = Chunk(text="A", id=1)
    b = Chunk(text="B", id=2)
    list1 = (a, b)
    list2 = (b, a)
    fused = await fuser.fuse([list1, list2], limit=10)
    fused_ids = [c.id for c in fused]
    assert set(fused_ids) == {1, 2}
    # Both items get equal score (1/60 + 1/61 each) — float equality is fine
    # because the additions are identical.
    assert all(c.relevance == (1 / 60 + 1 / 61) for c in fused)


@pytest.mark.asyncio
async def test_rrf_formula_higher_rank_wins() -> None:
    """Item ranked first in both lists should outrank item ranked second."""
    fuser = RRFResultFuser(k=60)
    # `a` is rank 0 in both lists; `b` is rank 1 in both.
    list1 = (Chunk(text="A", id=1), Chunk(text="B", id=2))
    list2 = (Chunk(text="A", id=1), Chunk(text="B", id=2))
    fused = await fuser.fuse([list1, list2], limit=10)
    assert fused[0].id == 1
    assert fused[1].id == 2
    # score(a) = 2 * 1/60; score(b) = 2 * 1/61
    assert fused[0].relevance == pytest.approx(2 / 60)
    assert fused[1].relevance == pytest.approx(2 / 61)


@pytest.mark.asyncio
async def test_rrf_step_reads_named_scratch_keys() -> None:
    """RRFFusionStep reads from scratch[<branch_name>.ranked] then writes state.candidates."""
    bm25 = (Chunk(text="A", id=1), Chunk(text="B", id=2))
    dense = (Chunk(text="B", id=2), Chunk(text="C", id=3))
    state = RetrieverState(
        query=_q(),
        scratch={"bm25.ranked": bm25, "dense.ranked": dense},
    )
    step = RRFFusionStep(
        name="rrf_fusion",
        k=60,
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    assert out.candidates is not None
    items = out.candidates.items
    fused_ids = [c.id for c in items]
    assert set(fused_ids) == {1, 2, 3}
    # `B` (id=2) appears in both lists at rank 1 + rank 0 → highest fused score.
    assert items[0].id == 2


@pytest.mark.asyncio
async def test_rrf_step_no_branches_returns_state_unchanged() -> None:
    state = RetrieverState(query=_q())
    step = RRFFusionStep(name="rrf_fusion", k=60, branch_keys=("absent.x",))
    out = await step.run(state)
    assert out is state


@pytest.mark.asyncio
async def test_rrf_step_accepts_chunklist_payload_in_scratch() -> None:
    """TopKFilterStep.publish_to (future task) will publish ChunkList objects.

    The step should accept both bare tuples and objects with .items (ChunkList).
    """
    from pydocs_mcp.models import ChunkList

    bm25 = ChunkList(items=(Chunk(text="A", id=1), Chunk(text="B", id=2)))
    dense = ChunkList(items=(Chunk(text="B", id=2), Chunk(text="C", id=3)))
    state = RetrieverState(
        query=_q(),
        scratch={"bm25.ranked": bm25, "dense.ranked": dense},
    )
    step = RRFFusionStep(
        name="rrf_fusion",
        k=60,
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    assert out.candidates is not None
    assert {c.id for c in out.candidates.items} == {1, 2, 3}


def test_rrf_step_serde_roundtrip() -> None:
    step = RRFFusionStep(
        name="rrf_fusion",
        k=42,
        branch_keys=("bm25.ranked", "dense.ranked", "lex.ranked"),
    )
    d = step.to_dict()
    assert d["type"] == "rrf_fusion"
    assert d["k"] == 42
    assert d["branch_keys"] == ["bm25.ranked", "dense.ranked", "lex.ranked"]


def test_rrf_step_registered_under_rrf_fusion_key() -> None:
    """Old key 'rrf' / 'reciprocal_rank_fusion' is gone; only 'rrf_fusion' remains."""
    # Force-load the step module so its registration runs.
    from pydocs_mcp.retrieval import steps as _steps
    from pydocs_mcp.retrieval.serialization import step_registry

    assert "rrf_fusion" in step_registry.names()
    assert "rrf" not in step_registry.names()
    assert "reciprocal_rank_fusion" not in step_registry.names()


def test_rrf_fusion_step_from_dict_rejects_k_zero() -> None:
    """'k: 0' is a plausible experiment (classic RRF assumes rank starts at

    1, so 'pure reciprocal rank' tuning sets k=0) but crashes at query time:
    _rrf_fuse computes 1.0 / (k + rank) with rank from enumerate() starting
    at 0, so the first item of the first branch divides by zero. Every other
    step's from_dict philosophy is to fail loudly at build time, not at
    query time — so k <= 0 must raise ValueError out of from_dict, naming
    the offending value, instead of reaching _rrf_fuse at all.
    """
    with pytest.raises(ValueError, match="0"):
        RRFFusionStep.from_dict({"type": "rrf_fusion", "k": 0}, BuildContext())


def test_rrf_fusion_step_from_dict_rejects_negative_k() -> None:
    with pytest.raises(ValueError, match="-5"):
        RRFFusionStep.from_dict({"type": "rrf_fusion", "k": -5}, BuildContext())


def test_rrf_fusion_step_construction_rejects_k_zero() -> None:
    """Direct construction (not just from_dict) must also reject k<=0 —

    RRFResultFuser and RRFFusionStep are both reachable without going
    through YAML (e.g. hybrid retriever composition), so the guard belongs
    on the dataclass itself, not only the decoder.
    """
    with pytest.raises(ValueError, match="0"):
        RRFFusionStep(k=0)


@pytest.mark.asyncio
async def test_rrf_result_fuser_k_equals_one_does_not_crash() -> None:
    """k=1 is the smallest valid k (rank starts at 0, so k+rank=1 at rank 0).

    Regression test pinning the minimal valid boundary fuses without
    raising ZeroDivisionError.
    """
    fuser = RRFResultFuser(k=1)
    a = Chunk(text="A", id=1)
    b = Chunk(text="B", id=2)
    fused = await fuser.fuse([(a, b)], limit=10)
    assert [c.id for c in fused] == [1, 2]
