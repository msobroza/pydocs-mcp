"""Pin ``is_relevant`` — the single relevance predicate metrics consume.

Hermetic: no ``pydocs_mcp`` import. ``is_relevant(item, task)`` is pure
set membership of ``_item_key(item)`` in
``task.gold.extra["resolved_chunk_ids"]`` (a ``frozenset[str]`` the runner
injects between ``search()`` and scoring). Task 4 extends this with an
``ast_body`` fallback for RepoQA — NOT exercised here.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics._relevance import is_relevant
from pydocs_eval.systems.base_system import RetrievedItem


def _task(resolved: object) -> EvalTask:
    extra: dict[str, object] = {}
    if resolved is not None:
        extra["resolved_chunk_ids"] = resolved
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra=extra),
        corpus_source=lambda: Path(),
    )


def test_chunk_id_item_hits_when_key_in_resolved_set() -> None:
    item = RetrievedItem(rank=1, text="x", source_path="p", chunk_id=42)
    task = _task(frozenset({"chunk:42"}))
    assert is_relevant(item, task) is True


def test_rank_keyed_item_hits_when_key_in_resolved_set() -> None:
    # Composite/blob systems carry chunk_id=None -> the rank key is used.
    item = RetrievedItem(rank=3, text="x", source_path="p", chunk_id=None)
    task = _task(frozenset({"rank:3"}))
    assert is_relevant(item, task) is True


def test_miss_returns_false() -> None:
    item = RetrievedItem(rank=1, text="x", source_path="p", chunk_id=7)
    task = _task(frozenset({"chunk:8", "rank:1"}))
    assert is_relevant(item, task) is False


def test_absent_resolved_key_returns_false() -> None:
    # WHY: a task whose gold.extra has no ``resolved_chunk_ids`` (no resolver
    # ran) must default to the empty frozenset -> never relevant.
    item = RetrievedItem(rank=1, text="x", source_path="p", chunk_id=42)
    task = _task(None)
    assert is_relevant(item, task) is False


def test_chunk_id_and_rank_namespaces_do_not_collide() -> None:
    # A resolved set keyed by rank must NOT mark a chunk-id item relevant
    # even when the integers coincide (chunk:5 vs rank:5).
    item = RetrievedItem(rank=9, text="x", source_path="p", chunk_id=5)
    task = _task(frozenset({"rank:5"}))
    assert is_relevant(item, task) is False
