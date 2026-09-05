"""The single seam between the optimize layer and the slice-5 agent-track harness.

This module is the ONLY place under ``benchmarks/optimize/`` that imports
``pydocs_eval.agent_track`` (spec §"Required upstream contract"). Everything
else in the optimize layer depends on the names re-exported here, never on the
agent-track package directly. That keeps the upstream contract pinned in one
file: if slice 5 ever renames a shape, the rename is absorbed HERE (import-and-
rename) and nowhere else, so no optimize step has to change.

As-landed contract (plain re-exports of ``pydocs_eval.agent_track``'s declared
public surface — the package ``__init__`` is the export list, so this binding
no longer reaches into the underscore submodules):

- ``AgentTrackConfig`` / ``ArmConfig`` / ``PairResult`` / ``RunMetrics`` /
  ``JudgeScore`` value objects. ``AgentTrackConfig`` carries ``rng_seed`` plus
  the guardrails (``max_usd`` / ``max_tasks`` / ``task_timeout_seconds`` /
  ``arms`` / ``judge_model``); ``task_prompt`` takes a keyword-only
  ``skill: str = ""``. ``ArmConfig`` is re-exported for the one-shot tool-less
  critique arm (``optimizers/critique_refine.py``), which reuses the same
  ``AgentRunner`` invocation pattern as ``RealJudge`` — the binding stays the
  single import point so that arm never reaches into ``eval.agent_track`` itself.
- ``AgentRunner`` Protocol + scripted ``FakeAgentRunner``.
- ``Judge`` Protocol + scripted ``FakeJudge``.
- ``task_prompt`` + ``run_agent_track`` + ``external_arm_hash`` (the
  external track's arm identity, half of its ledger resume key).
- ``fold_guidance`` + ``ExternalUndeliverableGuidanceError`` — the external
  harness's guidance partition (run-contract design §4). The fitness folds a
  candidate's sections HERE-side and hands the block to the runner's
  ``system_prompt_suffix``; the typed error is what a caller catches when a
  candidate carries a section this harness cannot deliver.
- ``SKILL_BLOCK_CHANNEL`` + ``TASK_PROMPT_SUFFIX_CHANNEL`` — the two names a
  pass reports to ``external_arm_hash`` for the channels it actually delivered
  on, so the same artifact carried two different ways is two different arms.
- The ``DEFAULT_*`` single-source run defaults the ask rubric run config
  mirrors (spec §3.5) — re-exported here so run_config never reaches into
  ``eval.agent_track`` itself.
"""

from __future__ import annotations

from pydocs_eval.agent_track import (
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    DEFAULT_RNG_SEED,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    SKILL_BLOCK_CHANNEL,
    TASK_PROMPT_SUFFIX_CHANNEL,
    AgentRunner,
    AgentTrackConfig,
    ArmConfig,
    ExternalUndeliverableGuidanceError,
    FakeAgentRunner,
    FakeJudge,
    Judge,
    JudgeScore,
    PairResult,
    RunMetrics,
    external_arm_hash,
    fold_guidance,
    run_agent_track,
    task_prompt,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MODEL",
    "DEFAULT_RNG_SEED",
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "SKILL_BLOCK_CHANNEL",
    "TASK_PROMPT_SUFFIX_CHANNEL",
    "AgentRunner",
    "AgentTrackConfig",
    "ArmConfig",
    "ExternalUndeliverableGuidanceError",
    "FakeAgentRunner",
    "FakeJudge",
    "Judge",
    "JudgeScore",
    "PairResult",
    "RunMetrics",
    "external_arm_hash",
    "fold_guidance",
    "run_agent_track",
    "task_prompt",
]
