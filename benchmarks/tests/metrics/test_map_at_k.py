"""Pin MAPAtK — average precision at k, crediting each gold item once.

``AP@k = (1/min(k, |gold|)) * SUM_{i<=k} rel_i * (SUM_{j<=i} rel_j)/i``
(arXiv:2607.11046 §5.1). Every expectation below is hand-computed from that
formula and written out as arithmetic, so a change to the implementation
fails against the definition rather than against a recorded number.

Three properties separate this metric from ``hit@k`` and are pinned here: AP
is rank-sensitive (earlier gold scores strictly higher); each distinct GOLD
item is credited at most once, keyed by the SAME identity the relevance
predicate dispatches on (so a chunk-split gold file counts once and a
resolved-chunk-id gold is not collapsed by path); and the ranking is the raw
CHUNK list every other ranked metric truncates, so ``map@k > 0`` implies
``hit@k == 1.0``.

Hermetic: no ``pydocs_mcp`` import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics import MAPAtK, NDCGAtK, RecallAtK
from pydocs_eval.metrics.hit_at_k import HitAtK
from pydocs_eval.systems.base_system import RetrievedItem


def _file_task(*gold: str) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(file_set=gold),
        corpus_source=lambda: Path(),
    )


def _resolved_task(*chunk_keys: str) -> EvalTask:
    """A DS-1000-shaped task: gold identity is a set of resolved CHUNK ids."""
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"resolved_chunk_ids": frozenset(chunk_keys)}),
        corpus_source=lambda: Path(),
    )


def _ranked(*paths: str) -> tuple[RetrievedItem, ...]:
    return tuple(
        RetrievedItem(rank=i, text="x", source_path=f"/tmp/corpus7/{p}")
        for i, p in enumerate(paths, start=1)
    )


def _chunked(*chunks: tuple[str, int]) -> tuple[RetrievedItem, ...]:
    """A ranking of ``(source_path, chunk_id)`` pairs — several chunks per file."""
    return tuple(
        RetrievedItem(rank=i, text="x", source_path=f"/tmp/corpus7/{path}", chunk_id=cid)
        for i, (path, cid) in enumerate(chunks, start=1)
    )


def test_single_gold_at_rank_1_is_perfect() -> None:
    # n_gt=1, denominator min(5,1)=1; precision at rank 1 = 1/1.
    task = _file_task("a.py")
    assert MAPAtK(k=5).compute(task, _ranked("a.py", "z.py")) == 1.0


def test_single_gold_at_rank_3() -> None:
    # precision at rank 3 = 1/3; denominator min(5,1)=1.
    task = _file_task("a.py")
    assert MAPAtK(k=5).compute(task, _ranked("x.py", "y.py", "a.py")) == 1 / 3


def test_both_gold_files_at_the_top_is_perfect() -> None:
    # (1/1 + 2/2) / min(5,2) = 2/2.
    task = _file_task("a.py", "b.py")
    assert MAPAtK(k=5).compute(task, _ranked("a.py", "b.py", "z.py")) == 1.0


def test_two_gold_interleaved_with_misses() -> None:
    # hits at ranks 2 and 4 -> (1/2 + 2/4) / min(5,2) = 1.0/2.
    task = _file_task("a.py", "b.py")
    assert MAPAtK(k=5).compute(task, _ranked("x.py", "a.py", "y.py", "b.py")) == 0.5


def test_one_of_two_gold_files_caps_at_half() -> None:
    # Only one of two gold files can ever be found -> (1/1) / min(5,2).
    task = _file_task("a.py", "b.py")
    assert MAPAtK(k=5).compute(task, _ranked("a.py", "x.py", "y.py")) == 0.5


def test_earlier_gold_scores_strictly_higher_than_later_gold() -> None:
    # THE rank-sensitivity property hit@k does not have: the same two gold
    # files, same k, different positions.
    task = _file_task("a.py", "b.py")
    early = MAPAtK(k=5).compute(task, _ranked("a.py", "b.py", "x.py", "y.py", "z.py"))
    late = MAPAtK(k=5).compute(task, _ranked("x.py", "y.py", "z.py", "a.py", "b.py"))
    assert early == 1.0
    assert late == (1 / 4 + 2 / 5) / 2
    assert early > late


def test_ties_in_the_ranking_are_broken_by_position_only() -> None:
    # Two gold files adjacent at ranks 1-2: which one is first cannot change
    # AP, because rel_i is binary and the positions are identical.
    task = _file_task("a.py", "b.py")
    assert MAPAtK(k=5).compute(task, _ranked("a.py", "b.py")) == MAPAtK(k=5).compute(
        task, _ranked("b.py", "a.py")
    )


def test_chunks_of_one_gold_file_count_once() -> None:
    # The gold file is credited at its EARLIEST rank (2) and never again ->
    # (1/2)/min(5,1). Crediting every chunk would give 1/2 + 2/3 = 1.17 and
    # breach the [0, 1] bound, which is the double-count the credit-once rule
    # exists for.
    task = _file_task("a.py")
    scored = MAPAtK(k=5).compute(task, _ranked("z.py", "a.py", "a.py"))
    assert scored == 0.5
    assert scored <= 1.0


def test_k_truncates_below_the_gold_rank() -> None:
    task = _file_task("a.py")
    assert MAPAtK(k=3).compute(task, _ranked("x.py", "y.py", "z.py", "a.py")) == 0.0


def test_k_below_the_gold_count_shrinks_the_denominator() -> None:
    # Three gold files but k=2: at most two can be found, so the denominator
    # is min(2,3)=2 and a perfect top-2 still scores 1.0.
    task = _file_task("a.py", "b.py", "c.py")
    assert MAPAtK(k=2).compute(task, _ranked("a.py", "b.py", "c.py")) == 1.0


def test_no_gold_at_all_returns_zero_without_dividing() -> None:
    # Empty gold -> ground_truth_count 0 -> the guard fires before min(k, 0).
    assert MAPAtK(k=5).compute(_file_task(), _ranked("a.py")) == 0.0


def test_empty_ranking_returns_zero() -> None:
    assert MAPAtK(k=5).compute(_file_task("a.py"), ()) == 0.0


def test_instance_name_includes_k() -> None:
    assert MAPAtK(k=5).name == "map@5"
    assert MAPAtK(k=10).name == "map@10"


# --- Gold identity: the credit unit must be the gold's own unit -----------


def test_a_perfect_resolved_chunk_id_ranking_scores_one() -> None:
    # THE regression: three gold CHUNKS of ONE file, ranked perfectly. Keying
    # the credit on source_path would collapse them to a single document and
    # cap AP at 1/3 while recall@k and ndcg@k both score 1.0.
    task = _resolved_task("chunk:1", "chunk:2", "chunk:3")
    ranked = _chunked(("pandas/core/frame.py", 1), ("pandas/core/frame.py", 2), ("f.py", 3))
    assert MAPAtK(k=5).compute(task, ranked) == 1.0
    assert RecallAtK(k=5).compute(task, ranked) == 1.0
    assert NDCGAtK(k=5).compute(task, ranked) == 1.0


def test_a_gold_chunk_behind_a_sibling_chunk_of_the_same_file_still_counts() -> None:
    # One gold chunk at rank 2, a NON-gold chunk of the same file at rank 1.
    # Collapsing by path would keep only the rank-1 miss and score 0.0.
    task = _resolved_task("chunk:7")
    ranked = _chunked(("f.py", 3), ("f.py", 7))
    assert MAPAtK(k=5).compute(task, ranked) == 0.5
    assert RecallAtK(k=5).compute(task, ranked) == 1.0


def test_ast_body_gold_is_a_single_credit_and_its_file_set_never_hijacks() -> None:
    # crosscommitvuln's shape: an ast_body RIDING ALONGSIDE a file_set. The
    # ast_body branch owns dispatch (n_gt=1), so two chunks carrying the gold
    # body are ONE credit at the earlier rank, and the file_set is inert.
    body = "def f(): return 1"
    task = EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=body, file_set=("never/match.py",)),
        corpus_source=lambda: Path(),
    )
    ranked = (
        RetrievedItem(rank=1, text="def g(): return 2", source_path="/c/x.py"),
        RetrievedItem(rank=2, text=body, source_path="/c/y.py"),
        RetrievedItem(rank=3, text=body, source_path="/c/z.py"),
    )
    assert MAPAtK(k=5).compute(task, ranked) == 0.5


def test_items_without_a_source_path_are_not_one_document() -> None:
    # ``source_path`` is "" whenever the DB column is NULL (systems/pydocs.py
    # defaults it), so path-keyed collapsing folded every such distractor into
    # ONE rank and promoted the gold up the ranking. Five empty-path
    # distractors then hid the rank-6 gold outside k=5 -> 0.0, not 0.5.
    task = _file_task("pkg/mod.py")
    blanks = tuple(RetrievedItem(rank=i, text="x", source_path="") for i in range(1, 6))
    gold = (RetrievedItem(rank=6, text="x", source_path="/tmp/c/pkg/mod.py"),)
    assert MAPAtK(k=5).compute(task, blanks + gold) == 0.0


# --- Rank-space coherence with hit@k -------------------------------------


@pytest.mark.parametrize("k", [1, 5, 10])
def test_a_positive_map_implies_a_hit_at_the_same_k(k: int) -> None:
    # Both metrics truncate the SAME chunk ranking at k, so AP@k > 0 cannot
    # happen without Hit@k == 1. A file-space map@k broke this: five chunks of
    # a.py then the gold at chunk rank 6 scored map@5 = 0.33 with hit@5 = 0.0.
    task = _file_task("pkg/b.py")
    ranked = _ranked("pkg/a.py", "pkg/a.py", "pkg/a.py", "pkg/a.py", "pkg/a.py", "pkg/b.py")
    scored = MAPAtK(k=k).compute(task, ranked)
    if scored > 0.0:
        assert HitAtK(k=k).compute(task, ranked) == 1.0
    assert (scored > 0.0) == (HitAtK(k=k).compute(task, ranked) == 1.0)
