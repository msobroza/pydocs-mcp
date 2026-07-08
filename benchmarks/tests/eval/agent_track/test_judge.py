"""Blind two-answer judge for the agent track (spec §D15).

Subprocess-free tests: the prompt builder and reply parser are pure, so the
blind-labeling contract (arm names never leak; A/B labels shuffle deterministically
from ``rng_seed``), the label-map round-trip (A/B back to arm names), and the
tolerant JSON parse (malformed reply → ``None``) are all asserted offline. The
real judge reuses ``ClaudeAgentRunner`` for its one-shot arm; ``FakeJudge`` is the
scripted double downstream tasks build their orchestrator tests on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval.agent_track import _judge
from benchmarks.eval.agent_track._command import build_claude_command
from benchmarks.eval.agent_track._judge import (
    FakeJudge,
    Judge,
    RealJudge,
    build_judge_prompt,
    parse_judge_reply,
)
from benchmarks.eval.agent_track._types import ArmConfig, JudgeScore, RunMetrics

# Pin the exported downstream seam (slice-6 contract): the ``Judge`` Protocol is
# imported so a rename fails this module, not just Task 6's orchestrator tests.
_ = Judge

_ANS = {"bare": "answer one", "indexed": "answer two"}
_RUBRIC_FIXTURE = Path(__file__).parent / "fixtures" / "judge_rubric.md"


def test_committed_rubric_matches_runtime_source() -> None:
    # The committed rubric fixture (reviewer-facing artifact) MUST stay
    # byte-identical to the runtime source of truth so a drift in one is caught.
    assert _RUBRIC_FIXTURE.read_text(encoding="utf-8") == _judge._RUBRIC_TEXT


def test_rubric_names_five_dimensions_and_bans_verbosity() -> None:
    rubric = _judge._RUBRIC_TEXT
    for dimension in ("correctness", "completeness", "relevance", "clarity", "reasoning"):
        assert dimension in rubric
    assert "Do not reward verbosity" in rubric


def _full(reply: str) -> str:
    # The judge answers inside a chat turn; the parser must dig the JSON block
    # out of surrounding prose, so wrap the raw JSON in narration here.
    return f"Here is my assessment.\n\n{reply}\n\nThat concludes the scoring."


def test_rubric_prompt_is_blind() -> None:
    prompt, label_map = build_judge_prompt(
        question="q?",
        gold="gold answer",
        answers={"bare": "answer one", "indexed": "answer two"},
        rng_seed=7,
    )
    assert "bare" not in prompt and "indexed" not in prompt  # arm names never leak
    assert "Answer A" in prompt and "Answer B" in prompt
    assert set(label_map.values()) == {"bare", "indexed"}
    assert set(label_map.keys()) == {"A", "B"}


def test_prompt_includes_question_gold_and_answers() -> None:
    prompt, label_map = build_judge_prompt(
        question="how does X sync?",
        gold="via a barrier",
        answers=_ANS,
        rng_seed=3,
    )
    assert "how does X sync?" in prompt and "via a barrier" in prompt
    # Both answer texts appear under their shuffled labels, never the arm names.
    assert "answer one" in prompt and "answer two" in prompt


def test_label_randomization_varies_with_seed() -> None:
    _, m1 = build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=1)
    _, m2 = build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=2)
    # Two seeds MAY collide onto the same shuffle, so we don't assert m1 != m2;
    # the load-bearing property is determinism — a rebuild at the same seed
    # reproduces the map (resume consistency). Both maps are still valid A/B maps.
    assert set(m1) == set(m2) == {"A", "B"}
    assert build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=1)[1] == m1


def test_parse_judge_reply_maps_labels_back() -> None:
    reply = (
        '{"A": {"correctness": 9, "completeness": 8, "relevance": 9, '
        '"clarity": 9, "reasoning": 8}, '
        '"B": {"correctness": 4, "completeness": 3, "relevance": 5, '
        '"clarity": 6, "reasoning": 4}}'
    )
    scores = parse_judge_reply(_full(reply), label_map={"A": "indexed", "B": "bare"})
    assert scores is not None
    assert scores["indexed"].correctness == 9
    assert scores["indexed"].mean == pytest.approx(8.6)  # mean of [9,8,9,9,8]
    assert scores["bare"].mean == pytest.approx(4.4)  # mean of [4,3,5,6,4]
    # The blind label the arm was shown as is recorded for auditability.
    assert scores["indexed"].blind_label_map == {"A": "indexed", "B": "bare"}


def test_malformed_reply_returns_none() -> None:
    assert parse_judge_reply("not json", label_map={"A": "bare", "B": "indexed"}) is None


def test_missing_label_returns_none() -> None:
    # A reply that scores only one label cannot produce a paired verdict.
    reply = (
        '{"A": {"correctness": 9, "completeness": 8, "relevance": 9, "clarity": 9, "reasoning": 8}}'
    )
    assert parse_judge_reply(_full(reply), label_map={"A": "bare", "B": "indexed"}) is None


async def test_fake_judge_scripts_scores() -> None:
    scripted = {
        "bare": JudgeScore(
            correctness=4,
            completeness=3,
            relevance=5,
            clarity=6,
            reasoning="",
            mean=4.4,
        ),
        "indexed": JudgeScore(
            correctness=9,
            completeness=8,
            relevance=9,
            clarity=9,
            reasoning="",
            mean=8.6,
        ),
    }
    judge = FakeJudge(scores=scripted)
    out = await judge.score(question="q", gold="g", answers=_ANS)
    assert out is not None
    assert out["indexed"].mean == pytest.approx(8.6)


async def test_fake_judge_can_return_none() -> None:
    # Judge failure is a first-class outcome the orchestrator must handle.
    judge = FakeJudge(scores=None)
    assert await judge.score(question="q", gold="g", answers=_ANS) is None


class _CapturingRunner:
    """Records the ``ArmConfig`` the judge hands the runner, then scripts a reply.

    Lets a subprocess-free test assert the judge builds a TOOL-LESS arm — the
    captured arm is fed straight into ``build_claude_command`` so the pinned
    surface is the real one the subprocess adapter would spawn.
    """

    def __init__(self) -> None:
        self.arm: ArmConfig | None = None

    async def run(self, arm, *, prompt, cwd, mcp_config) -> RunMetrics:
        _ = (prompt, cwd, mcp_config)
        self.arm = arm
        return RunMetrics(
            cost_usd=0.0,
            wall_seconds=0.0,
            turns=1,
            tool_calls=0,
            distinct_files_read=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            answer=(
                '{"A": {"correctness": 9, "completeness": 8, "relevance": 9, '
                '"clarity": 9, "reasoning": 8}, '
                '"B": {"correctness": 4, "completeness": 3, "relevance": 5, '
                '"clarity": 6, "reasoning": 4}}'
            ),
        )


async def test_real_judge_arm_is_tool_less(tmp_path: Path) -> None:
    # FINDING FIX: the blind judge must be tool-less. Capture the arm the judge
    # builds and assert the actual command it drives emits --allowedTools "" with
    # no file/shell/MCP grant — so the judge scores on answers + gold alone.
    runner = _CapturingRunner()
    judge = RealJudge(runner=runner, judge_model="claude-sonnet-5", rng_seed=0, cwd=tmp_path)
    await judge.score(question="q?", gold="g", answers=_ANS)
    assert runner.arm is not None
    cmd = build_claude_command(runner.arm, prompt="q?", cwd=tmp_path, mcp_config=None)
    idx = cmd.index("--allowedTools")
    assert cmd[idx + 1] == ""  # exactly the empty string — no tools granted
    joined = " ".join(cmd)
    for grant in ("Read", "Bash", "Grep", "Glob", "mcp__"):
        assert grant not in joined
