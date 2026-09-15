"""campaign/before-after — where an arm's ``ask_your_docs.llm`` block comes from.

The failure these tests pin: the serving YAML carried ``provider`` and
``params.*``, the eval binding refuses file-sourced model settings (an arm must
be deterministic), so EVERY rollout raised before any model call — and the arm
booked each raise as a nameless infra failure until the budget guard halted the
run at zero answered tasks, with the cause recorded nowhere. So: the block is
passed with ``--llm-block``, validated at plan time, and a rollout that raises
now says what raised.

Nothing here spends: the plan path is free by construction and the arm's runner
is injected.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import ArmSettings, run_arm
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

# The two shipped files the recorded run uses — the pair this fix exists for.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS = _REPO_ROOT / "benchmarks" / "configs"
_SERVING_CONFIG = _CONFIGS / "ask_openrouter_qwen3_4b.yaml"
_LLM_BLOCK = _CONFIGS / "ask_openrouter_qwen3_8_27b_llm.yaml"


# --- the plan -------------------------------------------------------------


@pytest.fixture
def offline_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan inputs resolved without a dataset, a tokenizer or a git repo."""

    async def _tasks(split: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
        return (_eval_task("t1"), _eval_task("t2"))[: limit or 2]

    monkeypatch.setattr(before_after_command, "load_split_tasks", _tasks)
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )


def _eval_task(task_id: str) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="where is the router?",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=lambda: Path("/corpus"),
    )


def _argv(tmp_path: Path, *extra: str, config: Path = _SERVING_CONFIG) -> list[str]:
    return [
        "before-after",
        "--baseline",
        "HEAD",
        "--candidate",
        "HEAD",
        "--config",
        str(config),
        "--split",
        "repoqa-qa/small_test",
        "--workspace",
        str(tmp_path / "ws"),
        "--model",
        "qwen/qwen3.8-27b",
        "--repo",
        str(_REPO_ROOT),
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def test_the_plan_prints_the_arm_llm_block(
    tmp_path: Path, offline_plan: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator sees the exact model settings the run will send, before spending."""
    assert main(_argv(tmp_path, "--llm-block", str(_LLM_BLOCK))) == 0

    printed = capsys.readouterr().out
    assert "NOTHING HAS BEEN SPENT" in printed
    assert f"llm block:  {_LLM_BLOCK}" in printed
    assert "params.temperature: 1.0" in printed
    assert "params.top_p: 0.95" in printed
    assert "params.max_tokens: 16384" in printed
    assert "provider: openrouter" in printed
    # Echoed as the operator wrote it: a YAML null, never Python's None.
    assert "parallel_tool_calls: null" in printed
    # The endpoint the run will really use comes from the block, not the serving file.
    assert "https://openrouter.ai/api/v1" in printed


def test_a_serving_file_that_sets_model_params_is_refused_at_plan_time(
    tmp_path: Path, offline_plan: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shape that failed every rollout must fail the PLAN instead — with the fix."""
    config = tmp_path / "serving_with_params.yaml"
    config.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    provider: openrouter\n"
        "    base_url: https://openrouter.ai/api/v1\n"
        "    params:\n"
        "      temperature: 1.0\n",
        encoding="utf-8",
    )

    assert main(_argv(tmp_path, config=config)) == 2

    message = capsys.readouterr().err
    assert "params.temperature" in message and "provider" in message
    assert "--llm-block" in message


def test_the_block_may_not_name_the_model(
    tmp_path: Path, offline_plan: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """One model, one flag: ``--model`` owns it and both arms share it."""
    block = tmp_path / "block_with_model.yaml"
    block.write_text("base_url: https://openrouter.ai/api/v1\nmodel: some/other-model\n")

    assert main(_argv(tmp_path, "--llm-block", str(block))) == 2

    message = capsys.readouterr().err
    assert "some/other-model" in message and "--model" in message


def test_a_typo_in_the_block_fails_the_plan_not_the_run(
    tmp_path: Path, offline_plan: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validation is the product's own model, so a bad key never reaches a rollout."""
    block = tmp_path / "typo.yaml"
    block.write_text("params:\n  temperatur: 1.0\n")

    assert main(_argv(tmp_path, "--llm-block", str(block))) == 2

    assert "temperatur" in capsys.readouterr().err


def test_a_block_file_that_is_not_a_mapping_says_what_was_expected(
    tmp_path: Path, offline_plan: None, capsys: pytest.CaptureFixture[str]
) -> None:
    block = tmp_path / "list.yaml"
    block.write_text("- base_url: https://openrouter.ai/api/v1\n")

    assert main(_argv(tmp_path, "--llm-block", str(block))) == 2

    message = capsys.readouterr().err
    assert "got a list" in message and "parallel_tool_calls" in message


# --- both arms, byte-identical ---------------------------------------------


def test_both_arms_are_handed_the_byte_identical_block(
    tmp_path: Path, offline_plan: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two arms differing in their model settings would measure two experiments."""
    monkeypatch.setattr(before_after_command, "product_worktree", _no_worktree)
    monkeypatch.setattr(before_after_command, "_spawn_arm", _write_empty_summary)

    assert main(_argv(tmp_path, "--llm-block", str(_LLM_BLOCK), "--confirm-spend")) == 0

    blocks = [
        json.loads((tmp_path / "out" / role / "arm_settings.json").read_text())["llm_block"]
        for role in ("baseline", "candidate")
    ]
    assert blocks[0] == blocks[1]
    assert blocks[0]["params"] == {
        "thinking": "auto",
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 16384,
    }
    assert blocks[0]["provider"] == "openrouter"
    assert "model" not in blocks[0]


@contextlib.contextmanager
def _no_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    """``product_worktree`` without git — the arm child is stubbed out anyway."""
    yield root / sha[:12]


def _write_empty_summary(worktree: Path, *, arm_dir: Path, split: str, limit: int | None) -> None:
    """Stand in for the whole arm child process: write the summary it would write."""
    payload = {
        "role": arm_dir.name,
        "commit": "a" * 40,
        "model": "qwen/qwen3.8-27b",
        "trace_root": "",
        "tasks": [],
        "estimated_usd": 0.0,
        "halt_reason": "completed",
        "excluded": 0,
    }
    (arm_dir / "arm.json").write_text(json.dumps(payload), encoding="utf-8")


# --- a raised rollout names its cause --------------------------------------


class RaisingHarnessRunner:
    """Every rollout raises, the way a refused arm block did before this fix."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def run(self, sample: dict, guidance: dict) -> object:
        raise self.error


def _arm_settings(tmp_path: Path) -> ArmSettings:
    return ArmSettings(
        role="baseline",
        commit="a" * 40,
        workspace=str(tmp_path / "ws"),
        model="qwen/qwen3.8-27b",
        trace_root=str(tmp_path / "traces"),
        out_dir=str(tmp_path / "arm"),
        max_agent_turns=4,
        estimated_usd_per_rollout=0.25,
        cost_ceiling_usd=10.0,
    )


async def test_a_raising_rollout_records_its_exception_in_the_queue(tmp_path: Path) -> None:
    """A run that answers nothing must say WHY, not repeat "infra retry"."""
    error = ValueError("pydocs_config sets ask_your_docs.llm params.temperature")
    settings = _arm_settings(tmp_path)

    summary = await run_arm(
        settings, (_eval_task("t1"),), make_runner=lambda s: RaisingHarnessRunner(error)
    )

    details = [
        json.loads(line)["detail"]
        for line in (Path(settings.out_dir) / "queue.jsonl").read_text().splitlines()
        if json.loads(line)["detail"]
    ]
    assert details == [
        "infra retry: ValueError: " + str(error),
        "infra excluded: ValueError: " + str(error),
    ]
    assert summary.tasks == [] and summary.excluded == 1


async def test_a_raised_rollout_books_exactly_what_the_plan_estimated(tmp_path: Path) -> None:
    """The fix is about visibility: the money side of a raise must not move."""
    settings = _arm_settings(tmp_path)

    await run_arm(
        settings,
        (_eval_task("t1"),),
        make_runner=lambda s: RaisingHarnessRunner(RuntimeError("boom")),
    )

    costs = [
        json.loads(line)["cost_usd"]
        for line in (Path(settings.out_dir) / "queue.jsonl").read_text().splitlines()
    ]
    # RUNNING books nothing; the retry and the exclusion each book the estimate.
    assert costs == [0.0, 0.25, 0.0, 0.25]
