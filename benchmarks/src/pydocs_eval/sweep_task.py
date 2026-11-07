"""One sweep task: index → search → resolve gold → score, then its tracker fan-out (spec §4.6).

``run_one_task`` owns no tracker I/O; ``run_and_log_task`` hands its
observation to the leg's trackers (``sweep_leg_tracking``), and on a scoring
failure still emits the latency measured before it (spec §5.5).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .metrics.base_metric import Scorer
from .sweep_leg_tracking import LegTracking, log_task_latency
from .sweep_support import (
    _capture_library_resolution,
    _maybe_set_library,
    _resolve_and_inject,
)
from .task_observation import TaskObservation

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from .datasets.base_dataset import EvalTask


class ScorerFailure(Exception):
    """Gold resolution / scoring raised AFTER latency was cleanly measured.

    Carries the partial observation (empty ``scores``) so ``_run_leg`` can
    still emit the latency a scoring crash must not suppress (the
    latency-before-scorer contract, spec §5.5), then re-raise ``cause``.
    """

    def __init__(self, partial: TaskObservation, cause: Exception) -> None:
        super().__init__(f"scoring failed for task {partial.task_id!r}")
        self.partial = partial
        self.cause = cause


async def run_one_task(
    system: object,
    task: EvalTask,
    config: AppConfig,
    scorer: Scorer,
    *,
    corpus_dir: Path | None,
) -> TaskObservation:
    """Index → search → resolve gold → score ONE task. No tracker I/O.

    Raises ``ScorerFailure`` (carrying the measured latency) when anything
    after the search timing bracket fails; index/search failures propagate
    bare — they have no cleanly-measured latency to preserve.
    """
    # WHY: comparative systems (Context7, Neuledge) need a library
    # identifier resolved from task metadata BEFORE ``index()``. Opt-in via
    # the ``HasLibraryName`` / ``HasLibrary`` Protocols.
    _maybe_set_library(system, task.metadata)
    # WHY: an operator-supplied corpus_dir overrides the task's own
    # ``corpus_source()`` for the whole sweep (e.g. DS-1000, whose
    # corpus_source is a /dev/null no-op). Absent → per-task source.
    dir_ = corpus_dir if corpus_dir is not None else task.corpus_source()
    try:
        # WHY: time.perf_counter is monotonic and the highest-resolution
        # clock available. We deliberately bracket only the system.index /
        # system.search awaits — not the scorer — so the latency series
        # reflects the system under test, not the harness.
        t0 = time.perf_counter()
        await system.index(dir_, config)
        index_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        retrieved = await system.search(task.query, limit=10)
        search_seconds = time.perf_counter() - t1

        # Spec D9: index time is always measured; ``cache_hit`` tells the
        # tracker/aggregation side to skip recording it for warm tasks.
        cache_hit = bool(getattr(system, "was_cache_hit", False))

        try:
            # WHY: capture the library id BEFORE _resolve_and_inject — that
            # helper spreads ``{**task.gold.extra}``, so anything injected
            # first survives (feeds ``library_resolution@1`` +
            # ``coverage_signal``).
            task = _capture_library_resolution(system, task)
            task = await _resolve_and_inject(system, task, retrieved)
            scores = scorer.score(task, retrieved)
        except Exception as exc:
            # WHY: latency was measured cleanly before resolution / scoring
            # blew up — hand it to the leg so the failure doesn't suppress
            # it (previously guaranteed by emitting latency log_metric
            # before the scorer ran).
            partial = TaskObservation(
                task_id=task.task_id,
                scores={},
                index_seconds=index_seconds,
                search_seconds=search_seconds,
                cache_hit=cache_hit,
                metadata=dict(task.metadata),
            )
            raise ScorerFailure(partial, exc) from exc

        return TaskObservation(
            task_id=task.task_id,
            scores=dict(scores),
            index_seconds=index_seconds,
            search_seconds=search_seconds,
            cache_hit=cache_hit,
            metadata=dict(task.metadata),
        )
    finally:
        # WHY: only rmtree a per-task corpus the dataset materialized —
        # NEVER an operator-supplied corpus_dir (reused across every
        # task/leg and owned by the operator).
        if corpus_dir is None:
            shutil.rmtree(dir_, ignore_errors=True)


async def run_and_log_task(
    system: object,
    task: EvalTask,
    config: AppConfig,
    scorer: Scorer,
    tracking: LegTracking,
    *,
    step: int,
    corpus_dir: Path | None,
) -> TaskObservation:
    """Run one task and fan it out to the leg's trackers; a scoring failure still logs latency."""
    try:
        obs = await run_one_task(system, task, config, scorer, corpus_dir=corpus_dir)
    except ScorerFailure as failure:
        # Latency was measured cleanly before scoring failed — emit it
        # before re-raising the original error (spec §5.5).
        log_task_latency(tracking.handles, tracking.trackers, failure.partial, step=step)
        # WHY noqa: re-raise the ORIGINAL scorer error with its own chain
        # intact — `from failure` would rewrite its __cause__ into a
        # carrier-exception cycle.
        raise failure.cause  # noqa: B904
    tracking.log_task(obs, step=step)
    return obs


__all__ = ("ScorerFailure", "run_and_log_task", "run_one_task")
