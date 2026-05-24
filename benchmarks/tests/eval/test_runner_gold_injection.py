"""Pin the runner's gold-injection step (spec §5, locked decision).

Hermetic: no ``pydocs_mcp`` import. ``run_sweep`` itself defers
``from pydocs_mcp.retrieval.config import AppConfig`` (not installed in the
benchmarks venv), so we drive the extracted injection helper
``_resolve_and_inject`` directly with in-memory fakes — same code path the
runner loop executes between ``system.search()`` and ``scorer.score()``.

Asserts:
  - a ``HasGoldResolver`` system's ``resolve()`` output lands in a FRESH
    task's ``gold.extra["resolved_chunk_ids"]`` (frozen gold -> ``replace``,
    never mutated), and is the exact set the metric then receives;
  - a system that is NOT ``HasGoldResolver`` is a strict no-op (the task
    object is returned unchanged — RepoQA path preserved).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics.base_metric import Scorer
from benchmarks.eval.runner import _resolve_and_inject
from benchmarks.eval.systems.base_system import RetrievedItem


# ── fakes (no pydocs_mcp) ──────────────────────────────────────────────────


_KNOWN = frozenset({"chunk:1", "chunk:7"})


@dataclass(frozen=True, slots=True)
class _FakeResolver:
    """A GoldResolver whose ``resolve`` returns a fixed, known set."""

    result: frozenset[str]

    async def resolve(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> frozenset[str]:
        return self.result


@dataclass
class _SystemWithResolver:
    """Opt-in: structurally satisfies ``HasGoldResolver``."""

    name: str = "fake-with-resolver"

    @property
    def gold_resolver(self) -> _FakeResolver:
        return _FakeResolver(_KNOWN)


@dataclass
class _SystemWithoutResolver:
    """No ``gold_resolver`` member -> NOT ``HasGoldResolver`` (RepoQA-like)."""

    name: str = "fake-without-resolver"


@dataclass(frozen=True, slots=True)
class _AssertingMetric:
    """A metric that asserts the injected set is present on the task it
    receives — raises (failing the test) otherwise."""

    name: str = "asserts-resolved"

    def compute(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> float:
        assert "resolved_chunk_ids" in task.gold.extra
        assert task.gold.extra["resolved_chunk_ids"] == _KNOWN
        return 1.0


def _task() -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"doc_contents": ("body",)}),
        corpus_source=lambda: Path("."),
        metadata={"library": "pandas"},
    )


# ── tests ──────────────────────────────────────────────────────────────────


async def test_inject_populates_resolved_chunk_ids_on_fresh_task() -> None:
    original = _task()
    augmented = await _resolve_and_inject(
        _SystemWithResolver(), original, retrieved=()
    )

    # Fresh task object (frozen gold -> dataclasses.replace, not mutation).
    assert augmented is not original
    assert original.gold is not augmented.gold
    assert "resolved_chunk_ids" not in original.gold.extra  # source untouched
    assert augmented.gold.extra["resolved_chunk_ids"] == _KNOWN
    # Pre-existing extra keys are preserved.
    assert augmented.gold.extra["doc_contents"] == ("body",)


async def test_injected_set_is_what_the_metric_receives() -> None:
    augmented = await _resolve_and_inject(
        _SystemWithResolver(), _task(), retrieved=()
    )
    scorer = Scorer(metrics=(_AssertingMetric(),))
    # The asserting metric raises if ``resolved_chunk_ids`` is missing/wrong.
    scores = scorer.score(augmented, ())
    assert scores == {"asserts-resolved": 1.0}


async def test_non_resolver_system_is_noop() -> None:
    original = _task()
    augmented = await _resolve_and_inject(
        _SystemWithoutResolver(), original, retrieved=()
    )
    # Identity preserved — RepoQA systems flow through unchanged.
    assert augmented is original
    assert "resolved_chunk_ids" not in augmented.gold.extra
