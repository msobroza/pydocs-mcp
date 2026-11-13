"""The ``label-alignment`` verb: a plan by default, an offline dry run, the labellers only on ``--confirm-spend``."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.alignment_label_command import (
    add_label_alignment_command,
    cmd_label_alignment,
)
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord, write_arm_summary
from pydocs_eval.campaign.before_after_split import load_split_tasks
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset
from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.judge.alignment_label_file import read_alignment_labels
from pydocs_eval.judge.alignment_labelling import LabellerClients
from pydocs_eval.judge.alignment_vocabulary import AlignmentStratum, LabelStatus
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
from pydocs_eval.trajectory.ask_outcome import TaskOutcome

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"
_DEPLOYMENT = Path(__file__).parents[2] / "configs" / "judge_openrouter.yaml"
_RESERVED = "example-needle-chat/reserved"


async def _fixture_tasks(split_spec: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
    """The repoqa mini fixture for any repoqa-qa split; the real vendored reserved records."""
    if split_spec == _RESERVED:
        return await load_split_tasks(split_spec, limit=limit)
    dataset = RepoQaQuestionDataset(source=RepoQADataset(fixture_path=_FIXTURE))
    tasks = tuple([task async for task in dataset.tasks()])
    return tasks[:limit] if limit is not None else tasks


async def _tasks() -> tuple[EvalTask, ...]:
    return await _fixture_tasks("repoqa-qa/small_dev")


def _arm(tmp_path: Path, role: str, tasks: Sequence[EvalTask]) -> Path:
    """An arm that answered every task, naming the needle on even tasks and a wrong function on odd."""
    records = []
    for index, task in enumerate(tasks):
        symbol = task.gold.extra["symbol"]
        named = symbol if index % 2 == 0 else "helper"
        answer = f"It is `{named}` in `{task.gold.file_set[0]}`."
        records.append(
            ArmTaskRecord(
                task_id=task.task_id,
                trajectory_id=f"{role}-{index}",
                trace_dir="",
                gold_files=list(task.gold.file_set),
                turns=3,
                wall_seconds=1.0,
                answer_chars=len(answer),
                answer=answer,
                outcome=TaskOutcome.ANSWERED,
            )
        )
    summary = ArmSummary(
        role=role,
        commit="60d5a00a" * 5,
        model="qwen/qwen3.8-27b",
        trace_root="",
        tasks=records,
        estimated_usd=0.1,
        halt_reason="completed",
        excluded=0,
    )
    out = tmp_path / role
    out.mkdir()
    write_arm_summary(out, summary)
    return out


@pytest.fixture
def arms(tmp_path: Path) -> list[Path]:
    tasks = asyncio.run(_tasks())
    return [_arm(tmp_path, "baseline", tasks), _arm(tmp_path, "candidate", tasks)]


def _args(arms: Sequence[Path], *extra: str) -> argparse.Namespace:
    argv = ["label-alignment", "--split", "repoqa-qa/small_dev", "--deployment", str(_DEPLOYMENT)]
    for arm in arms:
        argv += ["--arm", str(arm)]
    parser = argparse.ArgumentParser()
    add_label_alignment_command(parser.add_subparsers())
    return parser.parse_args([*argv, *extra])


@contextmanager
def _refusing_labellers(_judge: JudgeConfig) -> Iterator[LabellerClients]:
    raise AssertionError("a plan or a dry run must never open the paid labellers")
    yield  # pragma: no cover


def _run(args: argparse.Namespace, **injected: object) -> int:
    injected.setdefault("open_labellers", _refusing_labellers)
    injected.setdefault("unlocked", lambda: False)
    return cmd_label_alignment(args, load_tasks=_fixture_tasks, **injected)  # type: ignore[arg-type]


def test_the_campaign_cli_offers_the_verb(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["label-alignment", "--help"])

    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--confirm-spend" in out
    assert "--dry-run" in out


def test_the_plan_draws_the_pilot_and_spends_nothing(
    arms: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "labels.jsonl"

    code = _run(_args(arms, "--labels", str(labels)))

    out = capsys.readouterr().out
    assert code == 0
    assert "label-alignment repoqa-qa/small_dev (pilot)" in out
    assert "items to draw: 10" in out
    assert "openai/gpt-6-astra:batch" in out and "anthropic/claude-opus-5.5:batch" in out
    assert "reserved questions never drawn: 10" in out
    assert "Spends nothing" in out
    assert not labels.exists()


def test_a_dry_run_labels_the_pilot_offline_adjudicates_it_and_sizes_the_passes(
    arms: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "labels.jsonl"

    code = _run(_args(arms, "--dry-run", "--labels", str(labels)))

    out = capsys.readouterr().out
    rows = read_alignment_labels(labels)
    assert code == 0
    assert "DRY RUN" in out
    assert len(rows) == 10
    assert sum(row.stratum is AlignmentStratum.PLANTED_INJECTION for row in rows) == 4
    assert all(row.astra.status is row.opus.status is LabelStatus.ANSWERED for row in rows)
    assert all(row.agreed for row in rows)
    assert "kappa" in out
    assert "pass 1" in out and "pass 2" in out


def test_a_dry_run_never_writes_the_vendored_label_file(
    arms: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(_args(arms, "--dry-run"))

    assert code == 2
    assert "--labels" in capsys.readouterr().err


def test_a_later_pass_grows_the_label_file_without_asking_the_pilot_again(
    arms: list[Path], tmp_path: Path
) -> None:
    labels = tmp_path / "labels.jsonl"
    _run(_args(arms, "--dry-run", "--labels", str(labels)))
    pilot = read_alignment_labels(labels)

    code = _run(
        _args(arms, "--dry-run", "--labels", str(labels), "--pass", "pass_1", "--items", "14")
    )

    rows = read_alignment_labels(labels)
    assert code == 0
    assert len(rows) == 14
    assert [row.item_id for row in rows[:10]] == [row.item_id for row in pilot]
    assert [row.astra for row in rows[:10]] == [row.astra for row in pilot]


def test_pass_2_refuses_before_the_swe_qa_questions_unlock(
    arms: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(_args(arms, "--pass", "pass_2"))

    assert code == 2
    assert "SWE_QA_QUESTION_SLICES" in capsys.readouterr().err


def test_the_jev_first_pass_is_a_spend(
    arms: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(_args(arms, "--jev-first-pass"))

    assert code == 2
    assert "--confirm-spend" in capsys.readouterr().err


def _scripted_labellers(labels_path: Path):
    """Labellers answering every row of whatever the run drew, agreeing on everything."""

    @contextmanager
    def open_labellers(_judge: JudgeConfig) -> Iterator[LabellerClients]:
        def content(question_ids: Sequence[str]) -> dict[str, object]:
            return {
                qid: {
                    "verdict": "L1" if qid == "completeness" else "false",
                    "evidence": "e",
                    "confidence": "high",
                }
                for qid in question_ids
            }

        rows = read_alignment_labels(labels_path)
        scripted = {row.item_id: content(row.questions) for row in rows}
        yield LabellerClients(
            astra=FakeOpenRouterChatClient(scripted, "openai/gpt-6-astra", batch_cost_usd=0.9),
            opus=FakeOpenRouterChatClient(
                scripted, "anthropic/claude-opus-5.5", batch_cost_usd=0.6
            ),
        )

    return open_labellers


def test_confirm_spend_labels_with_the_pinned_labellers(
    arms: list[Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-synthetic")
    labels = tmp_path / "labels.jsonl"

    code = _run(
        _args(arms, "--confirm-spend", "--labels", str(labels)),
        open_labellers=_scripted_labellers(labels),
    )

    out = capsys.readouterr().out
    assert code == 0
    assert all(row.astra.answered and row.opus.answered for row in read_alignment_labels(labels))
    assert "$1.5000" in out
    assert "$0.1500 per item" in out
    assert "DRY RUN" not in out


def test_confirm_spend_refuses_without_the_key(
    arms: list[Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    code = _run(_args(arms, "--confirm-spend", "--labels", str(tmp_path / "labels.jsonl")))

    assert code == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
