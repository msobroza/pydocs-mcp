"""Pin library_resolution@1 (Task 7).

The metric scores Context7's router: did it resolve the task's library to
the right Context7 ``/org/project`` id? Matching is a case-insensitive
PATH-SEGMENT match (not equality, not raw substring) because resolved ids
are ``/org/project`` paths, never bare library names — and it consults a
small alias map for cross-naming gaps (``torch`` vs ``/pytorch/pytorch``).

Hermetic: no ``pydocs_mcp`` import — drives the metric with in-memory
``EvalTask`` fakes.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics import LibraryResolution1
from pydocs_eval.registries import metric_registry


def _task(resolved: object, library: str) -> EvalTask:
    extra: dict[str, object] = {}
    if resolved is not None:
        extra["resolved_library_id"] = resolved
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra=extra),
        corpus_source=lambda: Path(),
        metadata={"library": library},
    )


def test_pandas_segment_match_returns_1_0() -> None:
    task = _task("/pandas-dev/pandas", "pandas")
    assert LibraryResolution1().compute(task, ()) == 1.0


def test_pandas_resolution_wrong_library_returns_0_0() -> None:
    # Resolved /pandas-dev/pandas but the task's library is numpy -> miss.
    task = _task("/pandas-dev/pandas", "numpy")
    assert LibraryResolution1().compute(task, ()) == 0.0


def test_substring_false_positive_rejected() -> None:
    # "numpy" is a raw SUBSTRING of /pyro-ppl/numpyro but NOT a path
    # segment — segment matching must reject this unrelated id (a plain
    # substring test would wrongly score 1.0).
    task = _task("/pyro-ppl/numpyro", "numpy")
    assert LibraryResolution1().compute(task, ()) == 0.0


def test_torch_alias_matches_pytorch_org() -> None:
    # DS-1000/PyPI call it "torch"; Context7 resolves /pytorch/pytorch.
    task = _task("/pytorch/pytorch", "torch")
    assert LibraryResolution1().compute(task, ()) == 1.0


def test_missing_resolved_library_id_returns_0_0() -> None:
    task = _task(None, "pandas")
    assert LibraryResolution1().compute(task, ()) == 0.0


def test_empty_library_returns_0_0() -> None:
    task = _task("/pandas-dev/pandas", "")
    assert LibraryResolution1().compute(task, ()) == 0.0


def test_match_is_case_insensitive() -> None:
    task = _task("/Pandas-Dev/Pandas", "pandas")
    assert LibraryResolution1().compute(task, ()) == 1.0


def test_name_and_registry_key() -> None:
    assert LibraryResolution1().name == "library_resolution@1"
    # Registered under the exact key so runner's _build_metric fallthrough
    # resolves it via metric_registry.build.
    built = metric_registry.build("library_resolution@1")
    assert built.name == "library_resolution@1"
