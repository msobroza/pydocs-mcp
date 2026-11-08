"""``before-after --reuse-baseline <chat runner output>``, end to end through the CLI.

The vendored chat runner records ``task_scaffold: bare`` and ``architecture:
auto``; until the overlay could say ``bare`` (issue #386) no plan produced its
settings digest, so a runner output was refused. A plan whose baseline runs
the runner's effective settings — the shipped bare/auto overlay over the same
``--llm-block``, the same model, commit and turn cap — now reuses it and runs
the candidate alone. The digest is not weakened: a plan that leaves out one
component the runner sends is still refused, by name.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import yaml

pytest.importorskip("langgraph")

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import read_arm_summary
from pydocs_eval.campaign.before_after_corpora import IndexIdentity
from pydocs_eval.campaign.before_after_llm_block import load_arm_llm_block
from tests.tools._chat_runner_fakes import (
    BARE_AUTO_OVERLAY,
    CHAT_MODEL,
    LLM_BLOCK,
    FakeAgentBuilder,
    FakeTracedServe,
    load_runner,
    runner_options,
    serving_turns,
)

from ._fakes import FakeArmRun, FakeSplitTasks, before_after_argv, git_repo_with_two_descriptions


def _sha(repo: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _runner_output(tmp_path: Path, commit: str) -> Path:
    """A finished chat runner arm over the dev questions, run under ``commit``."""
    runner = load_runner()
    out = tmp_path / "runner-output"
    options = runner_options(runner, out, commit=commit)
    asyncio.run(
        runner.run_chat_arm(options, open_serve=FakeTracedServe(), build=FakeAgentBuilder())
    )
    return out


@pytest.fixture
def chat_plan_inputs(monkeypatch: pytest.MonkeyPatch, stub_command: FakeArmRun) -> FakeArmRun:
    """The plan reads the REAL ``--llm-block`` and the chat serving file's turn cap."""
    monkeypatch.setattr(
        before_after_command, "_arm_llm_block", lambda args: load_arm_llm_block(args.llm_block)
    )
    monkeypatch.setattr(
        before_after_command,
        "_serving_settings",
        lambda args, block: before_after_command.ServingSettings(
            endpoint="http://e",
            max_agent_turns=serving_turns(),
            identity=IndexIdentity(embedder_model="m", embedder_dim=8, scope_id="scope"),
        ),
    )
    return stub_command


def _reuse_argv(tmp_path: Path, repo: Path, overlay: Path, reused: Path) -> list[str]:
    return before_after_argv(
        tmp_path,
        repo,
        "--llm-block",
        str(LLM_BLOCK),
        "--baseline-settings",
        str(overlay),
        "--candidate-settings",
        str(overlay),
        "--model",
        CHAT_MODEL,
        "--reuse-baseline",
        str(reused),
        "--confirm-spend",
    )


def _split_of(arm: Path, split: FakeSplitTasks) -> None:
    """The plan's split: the questions the runner answered."""
    split.gold_by_task = {row.task_id: tuple(row.gold_files) for row in read_arm_summary(arm).tasks}


def test_a_plan_running_the_runners_settings_reuses_its_output(
    tmp_path: Path, chat_plan_inputs: FakeArmRun, before_after_split: FakeSplitTasks
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _runner_output(tmp_path, _sha(repo, "HEAD~1"))
    _split_of(reused, before_after_split)

    assert main(_reuse_argv(tmp_path, repo, BARE_AUTO_OVERLAY, reused)) == 0

    assert chat_plan_inputs.roles == ["candidate"], "no baseline rollout"
    assert (tmp_path / "out" / "baseline").resolve() == reused.resolve()
    assert (tmp_path / "out" / "before_after.md").exists()


def test_a_plan_without_the_served_vision_rule_is_refused_by_its_digest(
    tmp_path: Path,
    chat_plan_inputs: FakeArmRun,
    before_after_split: FakeSplitTasks,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The runner's agent is sent ``vision: true``; an arm without it routes ``auto`` otherwise."""
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _runner_output(tmp_path, _sha(repo, "HEAD~1"))
    _split_of(reused, before_after_split)
    overlay = tmp_path / "bare_auto_without_vision.yaml"
    payload = {k: v for k, v in yaml.safe_load(BARE_AUTO_OVERLAY.read_text()).items() if k != "llm"}
    overlay.write_text(yaml.safe_dump(payload), encoding="utf-8")

    assert main(_reuse_argv(tmp_path, repo, overlay, reused)) == 2

    assert "settings_digest: recorded" in capsys.readouterr().err
    assert chat_plan_inputs.roles == []
