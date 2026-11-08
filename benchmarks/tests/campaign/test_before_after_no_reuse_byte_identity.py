"""Without ``--reuse-baseline``, a run's plan text and arm files do not move.

The serving gate (issue #385, owner follow-up) only ever speaks when a baseline
is reused: a run that reuses nothing prints the plan it always printed and
writes the ``arm_settings.json`` / ``arm.json`` / report bytes it always wrote.
The golden files were captured from the branch BEFORE the serving gate landed;
the only per-run values (the temp directory, the commit and its subject) are
masked so the comparison is about what the code writes, not where it ran.

Each arm's ``serving.json`` (the serving fingerprint a later run reuses it
by) is a NEW file beside these, so it is deliberately not part of the pin.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_arm_launch, before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after_arm import ArmSummary, write_arm_summary
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

from ._fakes import FakeArmBlockProbe

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIGS = _REPO_ROOT / "benchmarks" / "configs"
_GOLDEN = Path(__file__).resolve().parent / "golden" / "no_reuse"
_PINNED_FILES = (
    "plan.txt",
    "before_after.md",
    "baseline/arm_settings.json",
    "baseline/arm.json",
    "candidate/arm_settings.json",
    "candidate/arm.json",
)


@pytest.fixture
def offline_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whole ``--confirm-spend`` run with no dataset, tokenizer, worktree or child."""

    async def _tasks(
        split: str, *, limit: int | None = None, draw: object = None
    ) -> tuple[EvalTask, ...]:
        return tuple(_eval_task(task_id) for task_id in ("t1", "t2"))

    monkeypatch.setattr(before_after_command, "load_split_tasks", _tasks)
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )
    monkeypatch.setattr(before_after_command, "probe_arm_block", FakeArmBlockProbe())
    monkeypatch.setattr(before_after_arm_launch, "product_worktree", _no_worktree)
    monkeypatch.setattr(before_after_arm_launch, "_spawn_arm", _write_finished_summary)


def _eval_task(task_id: str) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="where is the router?",
        gold=GoldAnswer(file_set=("a.py",)),
        corpus_source=lambda: Path("/corpus"),
    )


@contextlib.contextmanager
def _no_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    yield root / sha[:12]


def _write_finished_summary(
    worktree: Path, *, arm_dir: Path, split: str, limit: int | None
) -> None:
    """What the arm child writes, through the real writer, from its own settings."""
    settings = json.loads((arm_dir / "arm_settings.json").read_text(encoding="utf-8"))
    summary = ArmSummary(
        role=settings["role"],
        commit=settings["commit"],
        model=settings["model"],
        trace_root=settings["trace_root"],
        tasks=[],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=2,
        max_agent_turns=settings["max_agent_turns"],
        architecture=settings["architecture"],
        task_scaffold=settings["task_scaffold"],
        guidance_sections_path=settings["guidance_sections_path"],
        settings_digest=settings["settings_digest"],
    )
    write_arm_summary(arm_dir, summary)


def _argv(tmp_path: Path) -> list[str]:
    return [
        "before-after",
        "--baseline",
        "HEAD",
        "--candidate",
        "HEAD",
        "--config",
        str(_CONFIGS / "ask_openrouter_qwen3_4b.yaml"),
        "--llm-block",
        str(_CONFIGS / "ask_openrouter_qwen3_8_27b_llm.yaml"),
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
        "--allow-identical",
        "--confirm-spend",
    ]


def _masked(text: str, tmp_path: Path) -> str:
    """The run's own values replaced by placeholders: the checkout, the temp dir, HEAD
    and its subject."""
    head = _git("rev-parse", "HEAD")
    subject = _git("log", "-1", "--format=%s", head)
    masks = ((str(_REPO_ROOT), "<repo>"), (str(tmp_path), "<tmp>"), (head, "<sha>"))
    for value, mask in (*masks, (head[:12], "<short>")):
        text = text.replace(value, mask)
    return text.replace(subject, "<subject>")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


@pytest.mark.parametrize("name", _PINNED_FILES)
def test_a_run_without_reuse_writes_the_bytes_it_always_wrote(
    tmp_path: Path, offline_run: None, name: str
) -> None:
    assert main(_argv(tmp_path)) == 0

    written = _masked((tmp_path / "out" / name).read_text(encoding="utf-8"), tmp_path)
    assert written == (_GOLDEN / name).read_text(encoding="utf-8")


def test_the_printed_plan_is_the_one_it_always_printed(
    tmp_path: Path, offline_run: None, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [arg for arg in _argv(tmp_path) if arg != "--confirm-spend"]
    assert main(argv) == 0

    printed = _masked(capsys.readouterr().out, tmp_path)
    assert printed == (_GOLDEN / "plan.txt").read_text(encoding="utf-8") + "\n"
