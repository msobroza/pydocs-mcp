"""Pin Coverage: 1.0 iff the resolver found >=1 store ground-truth chunk
(``resolved_chunk_ids`` non-empty), else falls back to the system-supplied
``coverage_signal`` flag (Context7's library-resolution signal). Both
empty/absent -> 0.0.

Hermetic: no ``pydocs_mcp`` import.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics import Coverage
from benchmarks.eval.systems.base_system import RetrievedItem


def _task(extra: dict[str, object]) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra=extra),
        corpus_source=lambda: Path(),
    )


def test_resolved_non_empty_returns_1_0() -> None:
    task = _task({"resolved_chunk_ids": frozenset({"chunk:1"})})
    assert Coverage().compute(task, ()) == 1.0


def test_empty_resolved_with_coverage_signal_returns_1_0() -> None:
    # Context7-style: no enumerable store GT, but the library resolved.
    task = _task({"resolved_chunk_ids": frozenset(), "coverage_signal": True})
    assert Coverage().compute(task, ()) == 1.0


def test_both_empty_returns_0_0() -> None:
    task = _task({"resolved_chunk_ids": frozenset()})
    assert Coverage().compute(task, ()) == 0.0


def test_both_absent_returns_0_0() -> None:
    task = _task({})
    assert Coverage().compute(task, ()) == 0.0


def test_resolved_non_empty_ignores_unset_signal() -> None:
    # A store hit wins even without a coverage_signal key.
    item = RetrievedItem(rank=1, text="x", source_path="p", chunk_id=1)
    task = _task({"resolved_chunk_ids": frozenset({"chunk:1"})})
    assert Coverage().compute(task, (item,)) == 1.0


def test_name_is_coverage() -> None:
    assert Coverage().name == "coverage"
