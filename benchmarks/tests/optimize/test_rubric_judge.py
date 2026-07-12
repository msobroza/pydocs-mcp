"""Configurable rubric judge — one-shot prompt + strict parse-or-discard (spec AC-10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.optimize.rubric.judge import (
    ConfigurableRubricJudge,
    FakeRubricJudge,
    build_rubric_prompt,
    parse_rubric_reply,
)
from pydocs_eval.optimize.rubric.model import RubricCriterion

_CRITERIA = (
    RubricCriterion(name="correctness", weight=0.6, description="Factually correct."),
    RubricCriterion(name="grounding", weight=0.4, description="Traceable to retrieved paths."),
)


class TestPrompt:
    def test_prompt_names_every_criterion_with_description(self) -> None:
        prompt = build_rubric_prompt(question="q?", answer="a", criteria=_CRITERIA)
        assert "correctness" in prompt and "Factually correct." in prompt
        assert "grounding" in prompt and "Traceable to retrieved paths." in prompt

    def test_prompt_carries_scale_and_anti_verbosity_rule(self) -> None:
        prompt = build_rubric_prompt(question="q?", answer="a", criteria=_CRITERIA)
        assert "0-10" in prompt
        assert "do not reward verbosity" in prompt.lower()

    def test_prompt_contains_question_and_answer(self) -> None:
        prompt = build_rubric_prompt(question="how?", answer="like this", criteria=_CRITERIA)
        assert "how?" in prompt and "like this" in prompt


class TestParse:
    def test_full_reply_parses(self) -> None:
        scores = parse_rubric_reply('{"correctness": 8, "grounding": 6.5}', criteria=_CRITERIA)
        assert scores == {"correctness": 8.0, "grounding": 6.5}

    def test_prose_wrapped_json_parses(self) -> None:
        reply = 'Sure!\n{"correctness": 8, "grounding": 6}\nHope that helps.'
        assert parse_rubric_reply(reply, criteria=_CRITERIA) is not None

    def test_missing_criterion_discards(self) -> None:
        assert parse_rubric_reply('{"correctness": 8}', criteria=_CRITERIA) is None

    def test_non_numeric_criterion_discards(self) -> None:
        reply = '{"correctness": "good", "grounding": 6}'
        assert parse_rubric_reply(reply, criteria=_CRITERIA) is None

    def test_out_of_range_score_discards(self) -> None:
        reply = '{"correctness": 11, "grounding": 6}'
        assert parse_rubric_reply(reply, criteria=_CRITERIA) is None

    def test_no_json_block_discards(self) -> None:
        assert parse_rubric_reply("I cannot score this.", criteria=_CRITERIA) is None


@dataclass(slots=True)
class _ScriptedRunner:
    """Minimal agent-track ``AgentRunner`` stand-in: canned reply text or None."""

    reply: str | None
    cost_usd: float = 0.05
    calls: list[str] = field(default_factory=list)

    async def run(self, arm, *, prompt, cwd, mcp_config):
        self.calls.append(prompt)
        if self.reply is None:
            return None
        from pydocs_eval.agent_track._types import RunMetrics

        return RunMetrics(
            cost_usd=self.cost_usd,
            wall_seconds=1.0,
            turns=1,
            tool_calls=0,
            distinct_files_read=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            answer=self.reply,
        )


class TestConfigurableRubricJudge:
    async def test_scores_and_cost_flow_through(self, tmp_path: Path) -> None:
        runner = _ScriptedRunner(reply='{"correctness": 9, "grounding": 7}')
        judge = ConfigurableRubricJudge(runner=runner, judge_model="m", cwd=tmp_path)
        verdict = await judge.score(question="q", answer="a", criteria=_CRITERIA)
        assert verdict.scores == {"correctness": 9.0, "grounding": 7.0}
        assert verdict.cost_usd == 0.05
        assert verdict.discard_reason is None

    async def test_judge_arm_is_one_shot_and_tool_less(self, tmp_path: Path) -> None:
        captured: list[object] = []
        inner = _ScriptedRunner(reply='{"correctness": 9, "grounding": 7}')

        @dataclass(slots=True)
        class _ArmCapturingRunner:
            async def run(self, arm, *, prompt, cwd, mcp_config):
                captured.append(arm)
                return await inner.run(arm, prompt=prompt, cwd=cwd, mcp_config=mcp_config)

        judge = ConfigurableRubricJudge(runner=_ArmCapturingRunner(), judge_model="m", cwd=tmp_path)
        await judge.score(question="q", answer="a", criteria=_CRITERIA)
        arm = captured[0]
        assert arm.max_turns == 1 and arm.no_tools is True  # type: ignore[attr-defined]

    async def test_timed_out_runner_discards_with_reason(self, tmp_path: Path) -> None:
        judge = ConfigurableRubricJudge(
            runner=_ScriptedRunner(reply=None), judge_model="m", cwd=tmp_path
        )
        verdict = await judge.score(question="q", answer="a", criteria=_CRITERIA)
        assert verdict.scores is None
        assert verdict.discard_reason is not None

    async def test_malformed_reply_discards_never_partial(self, tmp_path: Path) -> None:
        judge = ConfigurableRubricJudge(
            runner=_ScriptedRunner(reply='{"correctness": 9}'), judge_model="m", cwd=tmp_path
        )
        verdict = await judge.score(question="q", answer="a", criteria=_CRITERIA)
        assert verdict.scores is None and verdict.discard_reason is not None


class TestFakeRubricJudge:
    async def test_scripted_replies_and_call_count(self) -> None:
        fake = FakeRubricJudge(scripted={"q1": {"correctness": 8.0, "grounding": 5.0}})
        verdict = await fake.score(question="q1", answer="a", criteria=_CRITERIA)
        assert verdict.scores == {"correctness": 8.0, "grounding": 5.0}
        assert fake.calls == 1

    async def test_unscripted_question_discards(self) -> None:
        fake = FakeRubricJudge(scripted={})
        verdict = await fake.score(question="q?", answer="a", criteria=_CRITERIA)
        assert verdict.scores is None and verdict.discard_reason is not None
