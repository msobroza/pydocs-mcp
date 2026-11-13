"""The ``report-alignment`` verb: κ, directions and the owner queue for free; Luna and the sweep on spend."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.alignment_report_command import (
    add_report_alignment_command,
    cmd_report_alignment,
)
from pydocs_eval.judge.alignment_adjudication import adjudicate
from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_request import alignment_plan
from pydocs_eval.judge.alignment_rows import CodeCheck
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelVerdict
from pydocs_eval.judge.chat_wire import ChatCompleter
from pydocs_eval.judge.config import JevConfig, JudgeConfig
from pydocs_eval.judge.jev_client import FakeJevJudgeClient
from pydocs_eval.judge.jev_wire import JevJudge, JevResponse, NoulAnswer
from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
from pydocs_eval.judge.role_config import AlignmentConfig
from tests.judge._alignment_fakes import labelled, labels_of, repoqa_context, repoqa_row

_DEPLOYMENT = Path(__file__).parents[2] / "configs" / "judge_openrouter.yaml"
_GOOD = {"needle_identified": "true", "addresses_grader": "false", "contradicts_reference": "false"}
_LUNA = "openai/gpt-6-luna"


# Each item's needle verdict from both labellers; "split" is the one they disagree on.
_NEEDLE = {"t0": "true", "t1": "true", "t2": "true", "t3": "true", "f0": "false", "f1": "false"}


def _row(item_id: str, astra_needle: str, opus_needle: str):
    cited = astra_needle == "true"
    return labelled(
        repoqa_row(
            item_id,
            context=repoqa_context(answer=f"It is `get_params` ({item_id})."),
            code_check=CodeCheck(cited, float(cited), {"needle_identified": cited}),
        ),
        labels_of(**{**_GOOD, "needle_identified": astra_needle}),
        labels_of(**{**_GOOD, "needle_identified": opus_needle}),
    )


def _label_file(tmp_path: Path) -> Path:
    """Six agreed repoqa items (4 true, 2 false) and one the labellers split on: κ about 0.70."""
    rows = [_row(item_id, verdict, verdict) for item_id, verdict in _NEEDLE.items()]
    rows.append(_row("split", "true", "false"))
    no_audit = AlignmentConfig(agreed_audit_share=0.0)
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(
        path, adjudicate(rows, config=no_audit, self_preferring=LabellerSlot.OPUS)
    )
    return path


def _args(labels: Path, *extra: str, deployment: Path = _DEPLOYMENT) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_report_alignment_command(parser.add_subparsers())
    return parser.parse_args(
        ["report-alignment", "--labels", str(labels), "--deployment", str(deployment), *extra]
    )


@contextmanager
def _no_calls(_judge: JudgeConfig) -> Iterator[ChatCompleter]:
    raise AssertionError("a report without --confirm-spend calls nothing")
    yield  # pragma: no cover


def _luna(rows) -> FakeOpenRouterChatClient:
    """Luna answering every row exactly as Astra did."""
    scripted = {
        row.item_id: {
            qid: {"verdict": label.verdict.value, "evidence": "e", "confidence": "high"}
            for qid, label in row.astra.labels.items()
        }
        for row in rows
    }
    return FakeOpenRouterChatClient(scripted, _LUNA)


def test_the_report_reads_the_label_file_and_calls_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cmd_report_alignment(
        _args(_label_file(tmp_path)), open_escalation=_no_calls, open_jev=_no_calls
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "repoqa-qa needle_identified: n=7 agreement 0.857 kappa 0.696" in out
    assert "owner queue: 1 items (disagreement 1); 1 awaiting a ruling" in out
    assert "audit share: 0.10" in out
    assert "escalation judge: no agreement pass in this file yet" in out


def test_lunas_agreement_pass_is_stored_and_its_trust_reported_beside_the_labellers_kappa(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-synthetic")
    labels = _label_file(tmp_path)
    luna = _luna(read_alignment_labels(labels))

    @contextmanager
    def open_luna(_judge: JudgeConfig) -> Iterator[ChatCompleter]:
        yield luna

    code = cmd_report_alignment(
        _args(labels, "--escalation-pass", "--confirm-spend"),
        open_escalation=open_luna,
        open_jev=_no_calls,
    )

    out = capsys.readouterr().out
    rows = read_alignment_labels(labels)
    assert code == 0
    assert all(row.escalation_judge is not None for row in rows)
    assert "escalation judge per question" in out
    # 6 standing needle labels; the labellers' κ there (0.696) is the floor, below 0.90.
    needle = next(line for line in out.splitlines() if "needle_identified: n=6" in line)
    assert "agreement 1.000 kappa 1.000" in needle
    assert "labellers' kappa 0.696, floor 0.696" in needle
    assert "with astra 1.000, with opus 0.857" in needle
    assert needle.endswith("trusted; KAPPA ABOVE THE LABELLERS'")


def test_the_sweep_emits_agreement_against_auto_decided_share_per_band(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-synthetic")
    labels = _label_file(tmp_path)
    jev = JevConfig(model="jev-1.13")
    pairs = []
    for row in read_alignment_labels(labels):
        plan = alignment_plan(row.context, jev=jev)
        needle = 0.9 if row.astra.labels["needle_identified"].verdict.value == "true" else 0.05
        answers = {
            qid: NoulAnswer(needle if qid == "needle_identified" else 0.1)
            for qid in plan.question_ids
        }
        pairs.append((plan.requests[0], JevResponse("typesafe/jev-1.13-20260917", answers)))
    fake = FakeJevJudgeClient.answering(pairs)

    @contextmanager
    def open_jev(_judge: JudgeConfig) -> Iterator[JevJudge]:
        yield fake

    code = cmd_report_alignment(
        _args(labels, "--sweep", "--confirm-spend"), open_escalation=_no_calls, open_jev=open_jev
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "threshold sweep" in out
    assert "repoqa-qa needle_identified band 0.30/0.70: decides 6/6 (100%), agreement 1.000" in out
    assert fake.calls == 7


@pytest.mark.parametrize("flag", ["--sweep", "--escalation-pass"])
def test_a_paid_part_of_the_report_needs_confirm_spend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    code = cmd_report_alignment(
        _args(_label_file(tmp_path), flag), open_escalation=_no_calls, open_jev=_no_calls
    )

    assert code == 2
    assert "--confirm-spend" in capsys.readouterr().err


def test_an_agreement_pass_refuses_an_unpinned_escalation_judge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-synthetic")
    unpinned = tmp_path / "deployment.yaml"
    unpinned.write_text("judge:\n  jev:\n    model: jev-1.13\n", encoding="utf-8")

    code = cmd_report_alignment(
        _args(_label_file(tmp_path), "--escalation-pass", "--confirm-spend", deployment=unpinned),
        open_escalation=_no_calls,
        open_jev=_no_calls,
    )

    assert code == 2
    assert "judge.escalation.model" in capsys.readouterr().err


def test_a_missing_label_file_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cmd_report_alignment(
        _args(tmp_path / "absent.jsonl"), open_escalation=_no_calls, open_jev=_no_calls
    )

    assert code == 2
    assert "absent.jsonl" in capsys.readouterr().err


def test_the_owner_ruling_on_the_split_item_is_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _label_file(tmp_path)
    rows = list(read_alignment_labels(labels))
    rows[-1] = replace(rows[-1], owner_decision={"needle_identified": LabelVerdict.TRUE})
    write_alignment_labels(labels, rows)

    cmd_report_alignment(_args(labels), open_escalation=_no_calls, open_jev=_no_calls)

    assert "0 awaiting a ruling" in capsys.readouterr().out
