"""Paired-agent fitness: worked-example score, judge-parity pre-gate, baseline
cache, and skill-candidate prompt threading (plan Task 6 / spec §D3).

All doubles are offline — a scripted runner/judge keyed on the injected skill
marker, no subprocess, no socket, no live LLM (slice-6 contract). The runner
switches on the prompt (seed run carries no skill; candidate run carries the
injected skill text), so one runner instance serves BOTH arms of BOTH the seed
and candidate ``run_agent_track`` passes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from benchmarks.eval.agent_track._types import (
    ArmConfig,
    JudgeScore,
    RunMetrics,
)
from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.optimize._agent_track_binding import AgentTrackConfig
from benchmarks.optimize.fitness.paired_agent import (
    ArtifactInjection,
    PairedAgentFitness,
)

# One task lands on the "train" split under the pinned sha256 % 2 predicate;
# a second lands on "holdout" so ``partition_task_ids`` sees a non-empty
# split on both sides (its loud-refusal guard fires on a one-sided pool).
_TRAIN_TASK_ID = "swe-qa-pro:0001"  # sha256 % 2 == 0 → train
_HOLDOUT_TASK_ID = "swe-qa-pro:0005"  # sha256 % 2 == 1 → holdout

# One paired task on the train split → two arms per ``run_agent_track`` pass
# (bare + indexed); the judge is a scripted double, so it is NOT a runner call.
_CANDIDATE_ONLY_CALLS = 2

# The marker the candidate injection threads into the prompt; the scripted
# runner/judge switch on its presence to distinguish the seed pass (no skill)
# from the candidate pass.
_CANDIDATE_MARKER = "CANDIDATE-MARKER"

_BASE_JUDGE_MEAN = 8.0


# --------------------------------------------------------------------------- #
# Scripted offline doubles
# --------------------------------------------------------------------------- #
def _metrics(*, tokens: int, tools: int, files: int, answer: str = "") -> RunMetrics:
    """A ``RunMetrics`` with the scoring-relevant fields set, rest zeroed.

    ``tokens`` lands entirely in ``cache_read_tokens`` so the fitness's
    ``cache_read + cache_write`` sum equals ``tokens``.
    """
    return RunMetrics(
        cost_usd=1.0,
        wall_seconds=0.0,
        turns=1,
        tool_calls=tools,
        distinct_files_read=files,
        cache_read_tokens=tokens,
        cache_write_tokens=0,
        answer=answer,
    )


_ZERO = _metrics(tokens=0, tools=0, files=0)


@dataclass
class _ScriptedRunner:
    """Returns seed metrics for the no-skill pass, candidate metrics otherwise.

    The indexed arm carries all the scoring-relevant counts; the bare arm is
    zeroed, so the fitness's "sum over both arms" equals the indexed values.
    Counts every ``run`` call so the baseline-cache test can assert the seed
    pass is not re-run.
    """

    baseline: RunMetrics
    candidate: RunMetrics
    total_calls: int = 0

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        self.total_calls += 1
        is_candidate = _CANDIDATE_MARKER in prompt
        if arm.mcp:  # indexed arm carries the scoring counts + a marker answer
            metrics = self.candidate if is_candidate else self.baseline
            return replace(metrics, answer=_CANDIDATE_MARKER if is_candidate else "seed")
        return replace(_ZERO, answer=_CANDIDATE_MARKER if is_candidate else "seed")


@dataclass
class _PromptCapturingRunner:
    """Records every prompt it is handed; returns fixed non-zero metrics."""

    prompts: list[str] = field(default_factory=list)
    total_calls: int = 0

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        self.total_calls += 1
        self.prompts.append(prompt)
        answer = _CANDIDATE_MARKER if _CANDIDATE_MARKER in prompt else "seed"
        return replace(_metrics(tokens=10, tools=1, files=1), answer=answer)


@dataclass
class _McpConfigCapturingRunner:
    """Records each ``.mcp.json`` the indexed arm is handed (as parsed JSON).

    The overlay wrapper rewrites this file in place BEFORE the inner runner sees
    it, so reading it here observes the rewritten arm-B server command.
    """

    configs: list[dict] = field(default_factory=list)

    async def run(
        self,
        arm: ArmConfig,
        *,
        prompt: str,
        cwd: Path,
        mcp_config: Path | None,
    ) -> RunMetrics | None:
        if mcp_config is not None:
            self.configs.append(json.loads(mcp_config.read_text(encoding="utf-8")))
        answer = _CANDIDATE_MARKER if _CANDIDATE_MARKER in prompt else "seed"
        return replace(_metrics(tokens=10, tools=1, files=1), answer=answer)


@dataclass
class _MarkerJudge:
    """Scripted judge: candidate answers score ``_BASE_JUDGE_MEAN + delta``.

    The indexed arm's answer carries ``_CANDIDATE_MARKER`` on the candidate
    pass, so the judge scores the candidate pass ``delta`` above the seed pass
    without any call counting — it reads the answers dict.
    """

    delta: float

    async def score(
        self,
        *,
        question: str,
        gold: str,
        answers: dict[str, str],
    ) -> dict[str, JudgeScore] | None:
        is_candidate = any(_CANDIDATE_MARKER in a for a in answers.values())
        mean = _BASE_JUDGE_MEAN + (self.delta if is_candidate else 0.0)
        return {name: _judge_score(mean) for name in answers}


def _judge_score(mean: float) -> JudgeScore:
    return JudgeScore(
        correctness=mean,
        completeness=mean,
        relevance=mean,
        clarity=mean,
        reasoning="",
        mean=mean,
    )


@dataclass
class _TwoTaskDataset:
    """A two-task dataset (one train, one holdout) with stub corpora."""

    name: str = "swe-qa-pro"
    revision: str = "test"

    def tasks(self) -> AsyncIterator[EvalTask]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[EvalTask]:
        for task_id in (_TRAIN_TASK_ID, _HOLDOUT_TASK_ID):
            yield EvalTask(
                task_id=task_id,
                query="What does X do?",
                gold=GoldAnswer(file_set=("x.py",)),
                corpus_source=_stub_corpus,
                metadata={"qa_type": "What"},
            )


def _stub_corpus() -> Path:
    # The orchestrator rmtrees the corpus dir per task; a tmp dir it can delete
    # keeps the scripted runner offline (the runner ignores cwd anyway). Pre-touch
    # the ``.pydocs-indexed`` marker so ``CorpusPrep.ensure_indexed`` short-circuits
    # and never spawns a real ``pydocs_mcp index`` subprocess (offline contract).
    import tempfile

    corpus = Path(tempfile.mkdtemp(prefix="paired-fit-"))
    (corpus / ".pydocs-indexed").touch()
    return corpus


# --------------------------------------------------------------------------- #
# Artifacts + injection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _SkillArtifact:
    """Minimal ``usage_skill``-shaped artifact whose render IS the skill text."""

    text: str
    name: str = "usage_skill"

    def render(self) -> str:
        return self.text

    def with_content(self, content: str) -> _SkillArtifact:
        return replace(self, text=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return "test"

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode()).hexdigest()


def _seed_artifact() -> _SkillArtifact:
    return _SkillArtifact(text="")


def _candidate_artifact() -> _SkillArtifact:
    return _SkillArtifact(text=_CANDIDATE_MARKER)


def _other_candidate() -> _SkillArtifact:
    return _SkillArtifact(text=f"{_CANDIDATE_MARKER} variant two")


def _skill_artifact(text: str) -> _SkillArtifact:
    return _SkillArtifact(text=text)


def _skill_inject(artifact) -> ArtifactInjection:
    """Thread the skill artifact's render() into the prompt via ``skill=``."""
    return ArtifactInjection(skill=artifact.render())


# --------------------------------------------------------------------------- #
# Fitness builder
# --------------------------------------------------------------------------- #
def _fitness(
    *,
    baseline: RunMetrics | None = None,
    candidate: RunMetrics | None = None,
    judge_delta: float = 0.0,
    ledger: Path,
    runner=None,
    artifact_kind: str = "usage_skill",
    inject=_skill_inject,
) -> PairedAgentFitness:
    _ = artifact_kind  # both kinds use the skill-based inject in these tests
    if runner is None:
        runner = _ScriptedRunner(
            baseline=baseline if baseline is not None else _metrics(tokens=10, tools=1, files=1),
            candidate=candidate if candidate is not None else _metrics(tokens=10, tools=1, files=1),
        )
    return PairedAgentFitness(
        runner=runner,
        judge=_MarkerJudge(delta=judge_delta),
        dataset=_TwoTaskDataset(),
        ledger_path=ledger,
        agent_cfg=AgentTrackConfig(max_tasks=8, max_usd=1_000_000.0),
        seed_artifact=_seed_artifact(),
        inject=inject,
    )


def _counting_fake_runner() -> _ScriptedRunner:
    return _ScriptedRunner(
        baseline=_metrics(tokens=10, tools=1, files=1),
        candidate=_metrics(tokens=8, tools=1, files=1),
    )


def _prompt_capturing_runner() -> _PromptCapturingRunner:
    return _PromptCapturingRunner()


# --------------------------------------------------------------------------- #
# Tests (spec §D3 requires the worked example)
# --------------------------------------------------------------------------- #
async def test_score_matches_worked_example(tmp_path) -> None:
    # tokens 100_000 → 80_000 → 0.20; tools 20 → 15 → 0.25; files 10 → 9 → 0.10
    # score = 0.5*0.20 + 0.3*0.25 + 0.2*0.10 = 0.195
    fit = _fitness(
        baseline=_metrics(tokens=100_000, tools=20, files=10),
        candidate=_metrics(tokens=80_000, tools=15, files=9),
        judge_delta=0.0,
        ledger=tmp_path / "trials.jsonl",
    )
    report = await fit.evaluate(_candidate_artifact(), split="train")
    assert report.score == pytest.approx(0.195)
    assert report.components["tokens_fraction"] == pytest.approx(0.20)


async def test_judge_parity_pre_gate_returns_neg_inf(tmp_path) -> None:
    fit = _fitness(judge_delta=-0.30, ledger=tmp_path / "l.jsonl")  # below the -0.25 floor
    report = await fit.evaluate(_candidate_artifact(), split="train")
    assert report.score == float("-inf")


async def test_baseline_computed_once_and_cached(tmp_path) -> None:
    runner = _counting_fake_runner()
    fit = _fitness(runner=runner, ledger=tmp_path / "l.jsonl")
    await fit.evaluate(_candidate_artifact(), split="train")
    calls_after_first = runner.total_calls
    await fit.evaluate(_other_candidate(), split="train")
    assert runner.total_calls - calls_after_first == _CANDIDATE_ONLY_CALLS  # no re-baseline


async def test_usage_skill_candidate_reaches_task_prompt(tmp_path) -> None:
    capturing = _prompt_capturing_runner()
    fit = _fitness(runner=capturing, artifact_kind="usage_skill", ledger=tmp_path / "l.jsonl")
    await fit.evaluate(_skill_artifact("ALWAYS start with get_overview"), split="train")
    assert any("ALWAYS start with get_overview" in p for p in capturing.prompts)


async def test_overlay_injection_names_overlay_server_in_arm_b_command(tmp_path) -> None:
    # spec §D6: when an overlay is injected, arm B's rendered .mcp.json is rewritten
    # so its server command boots the ``_overlay_server`` wrapper (not ``pydocs_mcp
    # serve``). The bare arm carries no config, so only arm B's command changes.
    capturing = _McpConfigCapturingRunner()
    overlay = tmp_path / "overlay.txt"
    overlay.write_text("ignored-by-the-scripted-runner")
    fit = _fitness(
        runner=capturing,
        ledger=tmp_path / "l.jsonl",
        inject=lambda artifact: ArtifactInjection(overlay_path=overlay),
    )
    await fit.evaluate(_candidate_artifact(), split="train")
    # Both passes (seed + candidate) run the indexed arm; every captured arm-B
    # command must name the overlay wrapper and carry the overlay path.
    assert capturing.configs
    for config in capturing.configs:
        args = config["mcpServers"]["pydocs-mcp"]["args"]
        assert "benchmarks.optimize._overlay_server" in args
        assert str(overlay) in args


async def test_no_overlay_leaves_arm_b_command_on_plain_serve(tmp_path) -> None:
    # Regression: without an overlay, arm B's command must stay the plain
    # ``pydocs_mcp serve`` the orchestrator renders — the overlay wrapper is
    # never spliced in when ``overlay_path`` is None.
    capturing = _McpConfigCapturingRunner()
    fit = _fitness(runner=capturing, ledger=tmp_path / "l.jsonl")
    await fit.evaluate(_candidate_artifact(), split="train")
    assert capturing.configs
    for config in capturing.configs:
        args = config["mcpServers"]["pydocs-mcp"]["args"]
        assert "benchmarks.optimize._overlay_server" not in args
        assert "serve" in args
