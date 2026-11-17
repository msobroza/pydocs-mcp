"""A task's reference answer rides its gold and never reaches the agent (step 7d, Q24).

``sample_row_for_task`` hands the binding the WHOLE gold, so the guarantee is a
boundary one: over the real product binding (behind its two documented seams,
the ``_chat_runner_fakes`` doubles), an arm answering swe-qa-questions and
repoqa-qa tasks that carry references sends the model none of the text, and
the candidate skill it writes beside every trajectory holds none of it either.
Nothing reaches a model, a child process or the network; the key is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from pydocs_eval.campaign.before_after_arm import ArmSettings, run_arm
from pydocs_eval.campaign.before_after_arm_settings import load_arm_overlay
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant, variant_of
from pydocs_eval.campaign.before_after_llm_block import load_arm_llm_block
from pydocs_eval.datasets.base_dataset import EvalTask, ReferenceAnswer, reference_answer_of
from pydocs_eval.datasets.reference_answers import ReferenceRow, write_reference_rows
from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset, SweQaQuestionDataset
from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.datasets.swe_qa import SweQaDataset
from pydocs_mcp.harness.ask_your_docs import agent, binding

from ._chat_runner_fakes import (
    CHAT_MODEL,
    CHAT_SERVE_CONFIG,
    COMMIT,
    LLM_BLOCK,
    FakeAgentBuilder,
    FakeBindingServeSession,
    serving_turns,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_REPO_ROOT = Path(__file__).parents[3]
_SEED_SKILL = _REPO_ROOT / "python/pydocs_mcp/harness/core/skills/search_guidance_seed.md"
_CANDIDATE_SKILL = "candidate_skill.md"
_KEY_ENV = "OPENROUTER_API_KEY"
_REPOQA_REFERENCE = ReferenceAnswer(
    text="`fixture_repo/math_helpers.py` — `factorial` multiplies every integer up to n.",
    model_id="anthropic/claude-opus-5.5",
    prompt_hash="0" * 64,
)


@dataclass
class _FakeRepoCache:
    """No git, no network; the one gold path the swe-qa fixture cites."""

    corpus_dir: Path = field(default=_FIXTURES / "swe_qa_corpus")

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ("lib/matplotlib/backends/backend_pgf.py",)


async def _swe_qa_questions() -> list[EvalTask]:
    source = SweQaDataset(
        fixture_path=_FIXTURES / "swe_qa_mini.jsonl",
        split="matplotlib",
        repo_cache=_FakeRepoCache(),
    )
    return [task async for task in SweQaQuestionDataset(source=source).tasks()]


async def _repoqa_question_with_a_reference(tmp_path: Path) -> EvalTask:
    source = RepoQADataset(fixture_path=_FIXTURES / "repoqa_mini.json")
    task_id = (await anext(RepoQaQuestionDataset(source=source).tasks())).task_id
    rows = tmp_path / "refs.jsonl"
    write_reference_rows(rows, [ReferenceRow(task_id, _REPOQA_REFERENCE)])
    framing = RepoQaQuestionDataset(source=source, reference_answers_path=rows)
    return await anext(framing.tasks())


def _citing_variant_with_guidance(tmp_path: Path) -> ArmVariant:
    """The default citing scaffold, plus a candidate skill the binding writes per rollout."""
    overlay = tmp_path / "arm.yaml"
    payload = {
        "architecture": "auto",
        "llm": {"vision": True},
        "guidance_sections": str(_SEED_SKILL),
    }
    overlay.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return variant_of(
        load_arm_overlay(overlay, flag="--candidate-settings"),
        llm_block=load_arm_llm_block(LLM_BLOCK),
        serving_turns=serving_turns(),
    )


def _arm_settings(tmp_path: Path) -> ArmSettings:
    out = tmp_path / "before-after"
    return ArmSettings(
        role="candidate",
        commit=COMMIT,
        workspace=str(tmp_path / "ws"),
        model=CHAT_MODEL,
        trace_root=str(out / "traces"),
        out_dir=str(out),
        estimated_usd_per_rollout=0.0,
        cost_ceiling_usd=1.0,
        pydocs_config=str(CHAT_SERVE_CONFIG),
        **_citing_variant_with_guidance(tmp_path).arm_settings_fields(),
    )


def _reference_texts(tasks: list[EvalTask]) -> list[str]:
    references = [reference_answer_of(task.gold) for task in tasks]
    assert all(references), "every task under test carries a reference"
    return [reference.text for reference in references if reference is not None]


@pytest.fixture
async def answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the arm over the referenced tasks; hand back its builder, tasks and trace root."""
    tasks = [*await _swe_qa_questions(), await _repoqa_question_with_a_reference(tmp_path)]
    builder = FakeAgentBuilder()
    monkeypatch.setattr(agent, "build_agent_with_scope_capabilities", builder)
    monkeypatch.setattr(binding, "_serve_session_tools", FakeBindingServeSession().session)
    monkeypatch.setenv(_KEY_ENV, "sk-or-synthetic-test-key")
    settings = _arm_settings(tmp_path)
    await run_arm(settings, tasks, refresh_indexes=lambda s: ())
    return builder, tasks, Path(settings.trace_root)


async def test_the_rendered_prompt_holds_none_of_the_reference_text(answered) -> None:
    builder, tasks, _ = answered

    assert len(builder.prompts) == len(tasks)
    for text in _reference_texts(tasks):
        assert all(text not in prompt for prompt in builder.prompts)


async def test_the_candidate_skill_holds_none_of_the_reference_text(answered) -> None:
    _, tasks, trace_root = answered
    skills = sorted(trace_root.rglob(_CANDIDATE_SKILL))

    assert len(skills) == len(tasks), "the binding wrote one candidate skill per rollout"
    written = [skill.read_text(encoding="utf-8") for skill in skills]
    for text in _reference_texts(tasks):
        assert all(text not in skill for skill in written)
