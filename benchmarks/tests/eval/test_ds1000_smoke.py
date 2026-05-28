"""End-to-end smoke tests for the three DS-1000 run modes (Task 11).

These tests drive ``run_sweep`` over the 50-task DS-1000 fixture with FAKE
systems injected into ``system_registry`` (popped in ``finally`` so the
global registry stays clean for every other test). They are PLUMBING tests:
the goal is that each of the three documented run modes wires together
end-to-end and returns a valid ``SweepResults`` with every requested metric
present per (system, config) row and in range — NOT that the fakes reproduce
real retrieval quality (that's covered by the resolver / metric unit tests).

The three modes map onto the two shipped AppConfig overlays:

  1. ``test_comparison_config_smoke``    — ``ds1000_composite.yaml``,
     systems ``pydocs-mcp-composite`` / ``context7`` / ``neuledge``.
  2. ``test_pydocs_only_config_smoke``   — ``ds1000_ranked.yaml``,
     system ``pydocs-mcp``.
  3. ``test_oracle_indexing_config_smoke`` — ``ds1000_ranked.yaml``,
     system ``pydocs-oracle``.

Hermetic: the dataset is built from the fixture via
``dataset_kwargs={"fixture_path": ...}`` (no HuggingFace download), the
systems are fakes (no real indexing, no network), and the JSONL tracker
writes under ``tmp_path`` so the run never pollutes the repo.

``SweepResults`` shape asserted against (see ``runner.SweepResults``):
``dict[(system_name, config_name), dict[metric_name, (mean, ci_low,
ci_high)]]`` — one row per (system, config) leg; each metric (and the
``indexing_seconds`` / ``search_seconds`` latency keys) maps to a 3-tuple.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems.base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

# 50-task stratified fixture (Task 10). Offline; no network/HF download.
_FIXTURE = Path(__file__).parent / "fixtures" / "ds1000_50.json"
_FIXTURE_TASK_COUNT = 50

# The two shipped DS-1000 AppConfig overlays. ``run_sweep`` keys each result
# row by ``cfg_path.stem``, so these stems are the config half of the
# SweepResults key.
_CONFIG_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "configs"
_COMPOSITE_CONFIG = _CONFIG_DIR / "ds1000_composite.yaml"
_RANKED_CONFIG = _CONFIG_DIR / "ds1000_ranked.yaml"


# ── fake systems ─────────────────────────────────────────────────────────
#
# All fakes mirror ``_CorpusRecordingSystem`` in test_runner_ds1000_filter.py:
# a plain mutable dataclass implementing the ``System`` Protocol (index /
# search / teardown) and NOTHING else. None of them expose ``gold_resolver``,
# so ``run_sweep`` treats them as non-``HasGoldResolver`` no-ops (confirmed
# tolerated — that's exactly how the corpus-recorder fake behaves). The
# metrics consequently see an empty resolved set and may score 0.0; that
# still satisfies ∈[0,1] and proves the wiring without re-litigating resolver
# correctness.


@dataclass
class _FakeCompositeSystem:
    """pydocs-mcp composite fake: one composite ``RetrievedItem`` (chunk_id
    None, like a budgeted blob)."""

    name: str = "pydocs-mcp-composite"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        return (
            RetrievedItem(
                rank=1,
                text="composite blob covering several pandas APIs",
                source_path="composite",
                qualified_name="pandas.composite",
                chunk_id=None,
            ),
        )

    async def teardown(self) -> None:
        return None


@dataclass
class _FakeContext7System:
    """Context7 fake: one concatenated blob AND a resolved ``/org/project``
    id. Exposing ``last_resolved_library_id`` makes it satisfy
    ``HasResolvedLibrary``, so the runner captures the id into
    ``gold.extra["resolved_library_id"]`` and ``library_resolution@1`` is
    meaningful for this row.
    """

    name: str = "context7"
    library_name: str = ""
    # WHY: present + truthy -> the runner's HasResolvedLibrary capture path
    # injects resolved_library_id + coverage_signal=True. ``/pandas-dev/pandas``
    # contains "pandas" so library_resolution@1 == 1.0 on the pandas-heavy
    # fixture (and 0.0 on non-pandas rows) — either way it's a present float.
    last_resolved_library_id: str | None = "/pandas-dev/pandas"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        return (
            RetrievedItem(
                rank=1,
                text="context7 concatenated documentation blob",
                source_path="context7",
            ),
        )

    async def teardown(self) -> None:
        return None


@dataclass
class _FakeNeuledgeSystem:
    """Neuledge fake: one concatenated blob. Carries a ``library`` field so
    the runner's ``_maybe_set_library`` seam (HasLibrary) has somewhere to
    write the DS-1000 ``metadata['library']`` — mirrors the real system.
    """

    name: str = "neuledge"
    library: str = ""

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        return (
            RetrievedItem(
                rank=1,
                text="neuledge concatenated documentation blob",
                source_path="neuledge",
            ),
        )

    async def teardown(self) -> None:
        return None


@dataclass
class _FakeRankedSystem:
    """pydocs-mcp native fake: a 5-item ranked tuple with distinct
    ``chunk_id``s (like real store rows from the ranked pipeline)."""

    name: str = "pydocs-mcp"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        return tuple(
            RetrievedItem(
                rank=i,
                text=f"ranked chunk {i}",
                source_path=f"src/mod_{i}.py",
                qualified_name=f"pkg.mod_{i}.fn",
                relevance=1.0 / i,
                chunk_id=i,
            )
            for i in range(1, 6)
        )

    async def teardown(self) -> None:
        return None


@dataclass
class _FakeOracleSystem:
    """pydocs-oracle fake: 5 ranked items whose ``qualified_name`` equals
    some of the task's gold ``doc_ids`` (so the items LOOK like real oracle
    hits). Distinct ``chunk_id``s, like real store rows.

    The gold ``doc_ids`` reach the fake via ``_seen_doc_ids``, which
    ``search()`` can't read (the Protocol only passes the query). So the
    fake instead emits a fixed set of synthetic-looking qualified names; the
    realism is cosmetic, matching the task spec ("items LOOK like real
    oracle hits"). Scope here is PLUMBING — deep exact-match resolver
    correctness is covered by the oracle resolver unit tests.
    """

    name: str = "pydocs-oracle"

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        return None

    async def search(
        self, query: str, limit: int,
    ) -> tuple[RetrievedItem, ...]:
        # These mirror the fixture's gold doc_id shape
        # (``pandas.api.synthetic_<n>.<Class>.<method>``) so a reader sees
        # oracle-shaped hits, but nothing here depends on a specific task.
        return tuple(
            RetrievedItem(
                rank=i,
                text=f"oracle doc body {i}",
                source_path="oracle",
                qualified_name=f"pandas.api.synthetic_{i}.DataFrame.method_{i}",
                relevance=1.0 / i,
                chunk_id=100 + i,
            )
            for i in range(1, 6)
        )

    async def teardown(self) -> None:
        return None


# ── registry override (snapshot + restore) ─────────────────────────────────


@contextlib.contextmanager
def _override_systems(fakes: Mapping[str, object]) -> Iterator[None]:
    """Temporarily point ``system_registry`` entries at the given fakes,
    restoring the ORIGINAL mapping on exit.

    WHY snapshot+restore (not pop): every fake here reuses a REAL registered
    system name (``context7`` / ``neuledge`` / ``pydocs-mcp`` /
    ``pydocs-mcp-composite`` / ``pydocs-oracle``). The template
    (``test_runner_ds1000_filter.py``) can ``.pop`` its fake in ``finally``
    only because ``corpus-recorder`` / ``fake-one-task`` don't exist in the
    registry — popping a NON-colliding name is a clean no-op. Popping a
    COLLIDING name would instead delete the genuine registration, so every
    later test (``test_system_registry`` / ``test_runner_smoke`` / …) would
    see an empty registry. Restoring a shallow copy of ``_items`` puts the
    real classes back exactly, whether we overwrote an existing key or not.
    """
    saved = dict(system_registry._items)
    for name, fake in fakes.items():
        # Bind the fake per-iteration (f=fake) so build() returns this object.
        system_registry._items[name] = lambda f=fake: f  # type: ignore[assignment]
    try:
        yield
    finally:
        system_registry._items.clear()
        system_registry._items.update(saved)


# ── assertion helpers ──────────────────────────────────────────────────────


def _assert_quality_metric(
    aggregates: dict[str, tuple[float, float, float]], key: str
) -> None:
    """Assert ``key`` is present and its (mean, ci_low, ci_high) are finite
    floats with the mean in [0, 1] and the CI bracketing it."""
    assert key in aggregates, f"missing metric {key!r} in {sorted(aggregates)}"
    mean, ci_low, ci_high = aggregates[key]
    for v in (mean, ci_low, ci_high):
        assert isinstance(v, float)
        assert v == v  # not NaN
        assert v not in (float("inf"), float("-inf"))
    assert 0.0 <= mean <= 1.0, f"{key} mean {mean} out of [0,1]"
    assert ci_low <= mean <= ci_high, f"{key} CI [{ci_low},{ci_high}] !bracket {mean}"


def _assert_latency_present(
    aggregates: dict[str, tuple[float, float, float]]
) -> None:
    """Latency percentile triples are emitted for every leg (spec §5.5)."""
    for key in ("indexing_seconds", "search_seconds"):
        assert key in aggregates, f"missing latency key {key!r}"
        p50, p95, p99 = aggregates[key]
        for v in (p50, p95, p99):
            assert isinstance(v, float)
            assert v >= 0.0


# ── 1. cross-system comparison ──────────────────────────────────────────────


async def test_comparison_config_smoke(tmp_path: Path) -> None:
    """Comparison run: pydocs-mcp-composite + context7 + neuledge on the
    composite overlay. Each system row carries the four shared ranked-collapse
    metrics in range; the context7 row additionally carries
    ``library_resolution@1`` (it set ``last_resolved_library_id``); the run
    consumes all 50 fixture tasks.
    """
    systems = ("pydocs-mcp-composite", "context7", "neuledge")
    metric_specs = (
        "recall@1",
        "mrr",
        "precision@1",
        "coverage",
        "library_resolution@1",
    )
    fakes = {
        "pydocs-mcp-composite": _FakeCompositeSystem(),
        "context7": _FakeContext7System(),
        "neuledge": _FakeNeuledgeSystem(),
    }

    # Snapshot + restore (NOT pop-in-finally). These fake names COLLIDE with
    # the real registered systems (context7 / neuledge / pydocs-mcp-composite),
    # so popping them in finally would delete the genuine registrations and
    # break every later test. The template's pop-in-finally is only safe for
    # NON-existent names ("corpus-recorder"); for an existing key we must
    # restore the prior mapping wholesale.
    with _override_systems(fakes):
        results, tasks_ran = await run_sweep(
            systems=systems,
            config_paths=(_COMPOSITE_CONFIG,),
            dataset_name="ds1000",
            dataset_kwargs={"fixture_path": _FIXTURE},
            tracker_names=("jsonl",),
            tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
            metric_specs=metric_specs,
        )

    # Valid SweepResults: a dict with one row per (system, config) leg.
    assert isinstance(results, dict)
    assert tasks_ran == _FIXTURE_TASK_COUNT

    config_name = _COMPOSITE_CONFIG.stem  # "ds1000_composite"
    for system_name in systems:
        key = (system_name, config_name)
        assert key in results, f"missing leg {key!r} in {sorted(results)}"
        aggregates = results[key]
        for metric in ("recall@1", "mrr", "precision@1", "coverage"):
            _assert_quality_metric(aggregates, metric)
        _assert_latency_present(aggregates)

    # library_resolution@1 present + in range for the context7 row (the only
    # system that resolved a library id).
    _assert_quality_metric(
        results[("context7", config_name)], "library_resolution@1"
    )


# ── 2. pydocs-only ranked ────────────────────────────────────────────────


async def test_pydocs_only_config_smoke(tmp_path: Path) -> None:
    """Pydocs-only ranked run: pydocs-mcp returning a 5-item ranked tuple on
    the ranked overlay. Full ranked metric suite present + in range,
    including ``ndcg@10``.
    """
    metric_specs = (
        "recall@1",
        "recall@5",
        "recall@10",
        "ndcg@10",
        "mrr",
        "precision@1",
        "coverage",
    )
    fake = _FakeRankedSystem()
    # "pydocs-mcp" is a REAL registered name -> snapshot + restore, never pop
    # (see test_comparison_config_smoke for the collision rationale).
    with _override_systems({"pydocs-mcp": fake}):
        results, tasks_ran = await run_sweep(
            systems=("pydocs-mcp",),
            config_paths=(_RANKED_CONFIG,),
            dataset_name="ds1000",
            dataset_kwargs={"fixture_path": _FIXTURE},
            tracker_names=("jsonl",),
            tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
            metric_specs=metric_specs,
        )

    assert isinstance(results, dict)
    assert tasks_ran == _FIXTURE_TASK_COUNT

    key = ("pydocs-mcp", _RANKED_CONFIG.stem)
    assert key in results
    aggregates = results[key]
    for metric in metric_specs:
        _assert_quality_metric(aggregates, metric)
    _assert_latency_present(aggregates)


# ── 3. oracle indexing ───────────────────────────────────────────────────


async def test_oracle_indexing_config_smoke(tmp_path: Path) -> None:
    """Oracle-indexing run: pydocs-oracle returning 5 oracle-shaped ranked
    items on the ranked overlay. Same metric shape + ranges as the
    pydocs-only run — confirms the oracle config plumbs through end-to-end.
    """
    metric_specs = (
        "recall@1",
        "recall@5",
        "recall@10",
        "ndcg@10",
        "mrr",
        "precision@1",
        "coverage",
    )
    fake = _FakeOracleSystem()
    # "pydocs-oracle" is a REAL registered name -> snapshot + restore, never pop
    # (see test_comparison_config_smoke for the collision rationale).
    with _override_systems({"pydocs-oracle": fake}):
        results, tasks_ran = await run_sweep(
            systems=("pydocs-oracle",),
            config_paths=(_RANKED_CONFIG,),
            dataset_name="ds1000",
            dataset_kwargs={"fixture_path": _FIXTURE},
            tracker_names=("jsonl",),
            tracker_kwargs={"jsonl": {"output_dir": tmp_path / "jsonl"}},
            metric_specs=metric_specs,
        )

    assert isinstance(results, dict)
    assert tasks_ran == _FIXTURE_TASK_COUNT

    key = ("pydocs-oracle", _RANKED_CONFIG.stem)
    assert key in results
    aggregates = results[key]
    for metric in metric_specs:
        _assert_quality_metric(aggregates, metric)
    _assert_latency_present(aggregates)
