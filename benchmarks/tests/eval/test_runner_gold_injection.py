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
from benchmarks.eval.runner import (
    _capture_library_resolution,
    _maybe_set_library,
    _resolve_and_inject,
)
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
        corpus_source=lambda: Path(),
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


# ── _maybe_set_library: DS-1000 metadata["library"] seeding (Task 7) ────────


@dataclass
class _SystemWithLibraryName:
    """Structurally satisfies ``HasLibraryName`` (Context7-like)."""

    name: str = "fake-libname"
    library_name: str = ""


@dataclass
class _SystemWithLibrary:
    """Structurally satisfies ``HasLibrary`` (Neuledge-like)."""

    name: str = "fake-lib"
    library: str = ""


def test_maybe_set_library_seeds_library_name_from_metadata_library() -> None:
    system = _SystemWithLibraryName()
    _maybe_set_library(system, {"library": "pandas"})
    assert system.library_name == "pandas"


def test_maybe_set_library_seeds_install_id_from_metadata_library() -> None:
    # HasLibrary branch: only `library`, no commit -> bare library string.
    system = _SystemWithLibrary()
    _maybe_set_library(system, {"library": "pandas"})
    assert system.library == "pandas"


def test_maybe_set_library_repo_takes_precedence_over_library() -> None:
    # RepoQA `repo` must still win when both are present.
    name_sys = _SystemWithLibraryName()
    lib_sys = _SystemWithLibrary()
    meta = {"repo": "psf/black", "library": "pandas", "commit": "abcdef1234"}
    _maybe_set_library(name_sys, meta)
    _maybe_set_library(lib_sys, meta)
    assert name_sys.library_name == "psf/black"
    assert lib_sys.library == "psf/black@abcdef1"


def test_maybe_set_library_no_repo_no_library_is_noop() -> None:
    system = _SystemWithLibraryName()
    _maybe_set_library(system, {})
    assert system.library_name == ""


# ── _capture_library_resolution (Task 7) ────────────────────────────────────


@dataclass
class _SystemWithResolvedLibrary:
    """Structurally satisfies ``HasResolvedLibrary``."""

    name: str = "fake-resolved"
    last_resolved_library_id: str | None = "/x/y"


def test_capture_injects_resolved_id_and_signal_true() -> None:
    original = _task()
    out = _capture_library_resolution(
        _SystemWithResolvedLibrary(last_resolved_library_id="/x/y"), original
    )
    assert out is not original  # frozen gold -> replace, never mutated
    assert out.gold.extra["resolved_library_id"] == "/x/y"
    assert out.gold.extra["coverage_signal"] is True
    # Pre-existing extra survives the spread.
    assert out.gold.extra["doc_contents"] == ("body",)
    assert "resolved_library_id" not in original.gold.extra


def test_capture_signal_false_when_resolution_empty() -> None:
    out = _capture_library_resolution(
        _SystemWithResolvedLibrary(last_resolved_library_id=None), _task()
    )
    assert out.gold.extra["resolved_library_id"] is None
    assert out.gold.extra["coverage_signal"] is False


def test_capture_non_matching_system_is_noop() -> None:
    # A system lacking ``last_resolved_library_id`` flows through unchanged.
    original = _task()
    out = _capture_library_resolution(_SystemWithoutResolver(), original)
    assert out is original
    assert "resolved_library_id" not in out.gold.extra
