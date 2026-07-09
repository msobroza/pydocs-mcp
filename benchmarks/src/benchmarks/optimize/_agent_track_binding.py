"""The single seam between the optimize layer and the slice-5 agent-track harness.

This module is the ONLY place under ``benchmarks/optimize/`` that imports
``benchmarks.eval.agent_track`` (spec §"Required upstream contract"). Everything
else in the optimize layer depends on the names re-exported here, never on the
agent-track submodules directly. That keeps the upstream contract pinned in one
file: if slice 5 ever renames a shape, the rename is absorbed HERE (import-and-
rename) and nowhere else, so no optimize step has to change.

As-landed contract (verified 2026-07-09 against ``main@92b40be``, the slice-5
merge — every name below exists with the stated signature; no import-and-rename
was needed, so these are plain re-exports):

- ``AgentTrackConfig`` / ``PairResult`` / ``RunMetrics`` / ``JudgeScore`` live in
  ``eval.agent_track._types``. ``AgentTrackConfig`` carries ``rng_seed`` plus the
  guardrails (``max_usd`` / ``max_tasks`` / ``task_timeout_seconds`` / ``arms`` /
  ``judge_model``); ``task_prompt`` takes a keyword-only ``skill: str = ""``.
- ``AgentRunner`` Protocol + scripted ``FakeAgentRunner`` in ``eval.agent_track._runner``.
- ``Judge`` Protocol + scripted ``FakeJudge`` in ``eval.agent_track._judge``.
- ``task_prompt`` in ``eval.agent_track._command``.
- ``run_agent_track`` in ``eval.agent_track.orchestrator``.

The package ``__init__`` ships no public re-exports (docstring only), so the
imports below target the submodules directly.
"""

from __future__ import annotations

from benchmarks.eval.agent_track._command import task_prompt
from benchmarks.eval.agent_track._judge import FakeJudge, Judge
from benchmarks.eval.agent_track._runner import AgentRunner, FakeAgentRunner
from benchmarks.eval.agent_track._types import (
    AgentTrackConfig,
    JudgeScore,
    PairResult,
    RunMetrics,
)
from benchmarks.eval.agent_track.orchestrator import run_agent_track

__all__ = [
    "AgentRunner",
    "AgentTrackConfig",
    "FakeAgentRunner",
    "FakeJudge",
    "Judge",
    "JudgeScore",
    "PairResult",
    "RunMetrics",
    "run_agent_track",
    "task_prompt",
]
