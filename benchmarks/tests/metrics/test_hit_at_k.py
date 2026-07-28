"""Pin HitAtK — the paper's Hit@k, and its equality with this repo's recall@k.

Hit@k (arXiv:2607.11046 §5.1) is binary per instance: 1.0 iff at least one
gold item lands at rank <= k. The run-level "proportion of instances" is the
mean ``metrics/aggregate.py`` takes, so nothing here aggregates.

The second half of this file is the honest finding the metric was added
under: ``recall@k`` in this repo has ALWAYS been Hit@k, not fractional
recall, so the two names cannot diverge — they now share one formula, and
these tests enumerate the shapes that would separate a fractional recall from
a hit (multi-file gold with only some found, several chunks from one gold
file, the k boundary) and pin equality in every one.

Hermetic: no ``pydocs_mcp`` import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics import HitAtK, RecallAtK
from pydocs_eval.systems.base_system import RetrievedItem


def _file_task(*gold: str) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(file_set=gold),
        corpus_source=lambda: Path(),
    )


def _item(rank: int, path: str) -> RetrievedItem:
    return RetrievedItem(rank=rank, text="x", source_path=f"/tmp/corpus7/{path}")


# --- Hit@k semantics ------------------------------------------------------


def test_one_gold_file_at_rank_1_is_a_hit() -> None:
    task = _file_task("src/pkg/mod.py")
    assert HitAtK(k=5).compute(task, (_item(1, "src/pkg/mod.py"),)) == 1.0


def test_gold_below_k_is_a_miss_and_at_k_is_a_hit() -> None:
    # The boundary: rank == k counts, rank == k+1 does not.
    task = _file_task("src/pkg/mod.py")
    ranked = tuple(_item(i, f"other{i}.py") for i in range(1, 5)) + (_item(5, "src/pkg/mod.py"),)
    assert HitAtK(k=5).compute(task, ranked) == 1.0
    assert HitAtK(k=4).compute(task, ranked) == 0.0


def test_one_of_several_gold_files_still_scores_a_full_hit() -> None:
    # THE definitional point: Hit@k asks "at least one", never "how many".
    # Three gold files, one found -> 1.0, not 1/3.
    task = _file_task("a/one.py", "b/two.py", "c/three.py")
    assert HitAtK(k=5).compute(task, (_item(1, "b/two.py"),)) == 1.0


def test_no_gold_in_the_ranking_is_zero() -> None:
    task = _file_task("a/one.py")
    assert HitAtK(k=5).compute(task, (_item(1, "b/two.py"), _item(2, "c/three.py"))) == 0.0


def test_empty_ranking_is_zero() -> None:
    assert HitAtK(k=5).compute(_file_task("a/one.py"), ()) == 0.0


def test_instance_name_includes_k() -> None:
    assert HitAtK(k=1).name == "hit@1"
    assert HitAtK(k=20).name == "hit@20"


# --- Equality with recall@k ----------------------------------------------

_DIVERGENCE_CANDIDATES = (
    # (label, gold file set, ranked paths) — every shape under which a
    # FRACTIONAL recall would differ from a hit.
    ("one of three gold files found", ("a.py", "b.py", "c.py"), ("a.py", "z.py")),
    ("all three gold files found", ("a.py", "b.py", "c.py"), ("a.py", "b.py", "c.py")),
    ("one gold file split across chunks", ("a.py",), ("a.py", "a.py", "a.py")),
    ("gold exactly at the k boundary", ("e.py",), ("x.py", "y.py", "z.py", "w.py", "e.py")),
    ("gold one past the k boundary", ("f.py",), ("x.py", "y.py", "z.py", "w.py", "v.py", "f.py")),
    ("nothing found", ("a.py",), ("x.py", "y.py")),
    ("nothing retrieved", ("a.py",), ()),
)


@pytest.mark.parametrize(("label", "gold", "paths"), _DIVERGENCE_CANDIDATES)
@pytest.mark.parametrize("k", [1, 5, 10])
def test_hit_at_k_equals_recall_at_k_on_every_divergence_candidate(
    label: str, gold: tuple[str, ...], paths: tuple[str, ...], k: int
) -> None:
    # NOT a tautology worth deleting: it is the pin that keeps a future edit
    # to either name from silently re-defining the other, and it is the
    # evidence for the claim in ``metrics/hit_at_k.py`` that the paper's Hit@k
    # is the metric this repo already reported under the name ``recall@k``.
    task = _file_task(*gold)
    ranked = tuple(_item(i, p) for i, p in enumerate(paths, start=1))
    assert HitAtK(k=k).compute(task, ranked) == RecallAtK(k=k).compute(task, ranked), label
