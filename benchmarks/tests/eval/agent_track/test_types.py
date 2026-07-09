"""Agent-track value objects (spec §D15)."""

import pytest

from pydocs_eval.agent_track._types import (
    AgentRunResult,
    AgentTrackConfig,
    ArmConfig,
    JudgeScore,
    PairResult,
    RunMetrics,
)

# Imported to pin the module's public surface per the plan's contract; the
# scoring-path shapes get behavior tests in later tasks.
_ = (AgentRunResult, JudgeScore)


def _metrics() -> RunMetrics:
    return RunMetrics(
        cost_usd=0.42,
        wall_seconds=61.0,
        turns=9,
        tool_calls=14,
        distinct_files_read=6,
        cache_read_tokens=120_000,
        cache_write_tokens=30_000,
        answer="The mask class ...",
    )


def test_run_metrics_fields() -> None:
    m = RunMetrics(
        cost_usd=0.42,
        wall_seconds=61.0,
        turns=9,
        tool_calls=14,
        distinct_files_read=6,
        cache_read_tokens=120_000,
        cache_write_tokens=30_000,
        answer="The mask class ...",
    )
    assert m.cost_usd == 0.42 and m.distinct_files_read == 6


def test_arm_config_command_relevant_fields() -> None:
    bare = ArmConfig(name="bare", model="claude-sonnet-5", max_turns=40, mcp=False)
    indexed = ArmConfig(name="indexed", model="claude-sonnet-5", max_turns=40, mcp=True)
    assert bare.mcp is False and indexed.mcp is True
    # no_tools defaults False so the two measured arms keep their current surface.
    assert bare.no_tools is False and indexed.no_tools is False


def test_arm_config_tool_less_profile() -> None:
    judge = ArmConfig(name="judge", no_tools=True)
    assert judge.no_tools is True and judge.mcp is False


def test_arm_config_rejects_tool_less_with_mcp() -> None:
    # A tool-less arm cannot also attach MCP — contradictory, fails at construction.
    with pytest.raises(ValueError):
        ArmConfig(name="judge", no_tools=True, mcp=True)


def test_track_config_defaults_and_guardrails() -> None:
    cfg = AgentTrackConfig()
    assert cfg.max_tasks == 48 and cfg.max_usd == pytest.approx(25.0)
    assert cfg.task_timeout_seconds == 900.0
    assert cfg.judge_model == cfg.arms[0].model  # same pinned family by default
    assert cfg.rng_seed == 0  # slice-6 contract: fixed seed for deterministic comparisons


def test_pair_result_requires_both_arms() -> None:
    with pytest.raises(ValueError):
        PairResult(task_id="t", qa_type="Why", bare=None, indexed=_metrics(), judge=None)
