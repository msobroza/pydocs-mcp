"""The ``write-references`` verb: a plan by default, the batches only on ``--confirm-spend``.

It writes the repoqa-qa references only: the ladder measures on repoqa-qa
(owner decision on the turn-efficiency umbrella, 2026-09-28), and the chat
records' references wait until a step is set to run on chat.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pytest

from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.reference_writer_command import (
    add_write_references_command,
    cmd_write_references,
)
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset
from pydocs_eval.datasets.repoqa import RepoQADataset

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"
_DEPLOYMENT = Path(__file__).parents[2] / "configs" / "judge_openrouter.yaml"


async def _fixture_tasks(split_spec: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
    dataset = RepoQaQuestionDataset(source=RepoQADataset(fixture_path=_FIXTURE))
    tasks = tuple([task async for task in dataset.tasks()])
    return tasks[:limit] if limit is not None else tasks


def _args(tmp_path: Path, *extra: str, split: str = "repoqa-qa/small_dev") -> argparse.Namespace:
    argv: Sequence[str] = [
        "write-references",
        "--split",
        split,
        "--deployment",
        str(_DEPLOYMENT),
        "--out",
        str(tmp_path / "refs.jsonl"),
        "--journal",
        str(tmp_path / "refs.journal.jsonl"),
        *extra,
    ]
    parser = argparse.ArgumentParser()
    add_write_references_command(parser.add_subparsers())
    return parser.parse_args(argv)


def test_the_campaign_cli_offers_the_verb(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["write-references", "--help"])

    assert exited.value.code == 0
    assert "--confirm-spend" in capsys.readouterr().out


def test_the_plan_names_the_pins_and_spends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    code = cmd_write_references(_args(tmp_path, "--limit", "2"), load_tasks=_fixture_tasks)

    out = capsys.readouterr().out
    assert code == 0
    assert "anthropic/claude-opus-5.5:batch" in out
    assert "anthropic/claude-sonnet-5:batch" in out
    assert "to write: 2" in out
    assert "--confirm-spend" in out
    assert not (tmp_path / "refs.jsonl").exists()
    assert not (tmp_path / "refs.journal.jsonl").exists()


@pytest.mark.parametrize("split", ["example-needle-chat/dev", "swe-qa-questions/all"])
def test_a_dataset_other_than_repoqa_qa_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], split: str
) -> None:
    code = cmd_write_references(_args(tmp_path, split=split), load_tasks=_fixture_tasks)

    assert code == 2
    assert split.split("/")[0] in capsys.readouterr().err


def test_an_unpinned_writer_is_refused_naming_its_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "judge.yaml"
    empty.write_text("reference_writer:\n  fallback_model: anthropic/claude-sonnet-5:batch\n")
    args = _args(tmp_path)
    args.deployment = empty

    code = cmd_write_references(args, load_tasks=_fixture_tasks)

    assert code == 2
    assert "reference_writer.model" in capsys.readouterr().err


def test_the_confirmed_run_needs_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    code = cmd_write_references(_args(tmp_path, "--confirm-spend"), load_tasks=_fixture_tasks)

    assert code == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "refs.journal.jsonl").exists(), "nothing was submitted"
