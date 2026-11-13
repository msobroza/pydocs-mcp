"""The alignment label file: one versioned JSON line per item, rewritten whole and byte-stable."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.judge.alignment_label_file import (
    ALIGNMENT_LABEL_REVISION,
    AlignmentLabelFileError,
    alignment_label_path,
    read_alignment_labels,
    vendored_alignment_label_path,
    write_alignment_labels,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, LabellerEntry, OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import (
    EscalationReason,
    LabelConfidence,
    LabelStatus,
    LabelVerdict,
)
from pydocs_eval.judge.role_config import AlignmentConfig

from ._alignment_fakes import (
    ASTRA_MODEL,
    OPUS_MODEL,
    answered,
    chat_row,
    label,
    labels_of,
    repoqa_row,
)

_GOLDEN_LINE = Path(__file__).parent / "goldens" / "alignment_label_line.json"


def _settled_row() -> AlignmentLabelRow:
    """A repoqa item both labellers answered, adjudicated, decided by the owner and run past Luna."""
    astra = answered(
        ASTRA_MODEL,
        {
            "needle_identified": label("true", evidence="`get_params` in `sklearn/base.py`"),
            "addresses_grader": label("false", LabelConfidence.MEDIUM, "none"),
            "contradicts_reference": label("false", LabelConfidence.LOW, "agrees"),
        },
        batch_id="batch_a",
        batch_cost_usd=0.75,
    )
    opus = answered(
        OPUS_MODEL,
        labels_of(
            needle_identified="true", addresses_grader="false", contradicts_reference="false"
        ),
        batch_id="batch_o",
        batch_cost_usd=0.3,
    )
    luna = answered("openai/gpt-6-luna", labels_of(needle_identified="true"), cost_usd=0.002)
    return repoqa_row(
        astra=astra,
        opus=opus,
        agreed={
            "needle_identified": LabelVerdict.TRUE,
            "addresses_grader": LabelVerdict.FALSE,
            "contradicts_reference": LabelVerdict.FALSE,
        },
        escalation_reasons=(
            OwnerEscalation(EscalationReason.LOW_CONFIDENCE, "contradicts_reference"),
            OwnerEscalation(EscalationReason.AUDIT),
        ),
        owner_decision={"contradicts_reference": LabelVerdict.TRUE},
        owner_note="The answer says it returns a list; the reference says a dict.",
        escalation_judge=luna,
    )


def test_every_field_of_a_row_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    rows = (_settled_row(), chat_row())

    write_alignment_labels(path, rows)

    assert read_alignment_labels(path) == rows


def test_a_line_of_the_label_file_matches_its_golden(tmp_path: Path) -> None:
    """The shape the annotation page reads and writes back: pinned field by field."""
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(path, (_settled_row(),))

    (line,) = path.read_text(encoding="utf-8").splitlines()

    assert json.loads(line) == json.loads(_GOLDEN_LINE.read_text(encoding="utf-8"))
    assert json.loads(line)["revision"] == ALIGNMENT_LABEL_REVISION == "1.0"


def test_reading_and_writing_back_moves_no_byte(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(path, (_settled_row(), chat_row(), repoqa_row("item_repoqa_2")))
    before = path.read_bytes()

    write_alignment_labels(path, read_alignment_labels(path))

    assert path.read_bytes() == before


def test_only_the_owner_fields_move_when_the_owner_decides(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(path, (chat_row(), repoqa_row()))
    before = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    first, second = read_alignment_labels(path)
    decided = replace(
        second, owner_decision={"needle_identified": LabelVerdict.FALSE}, owner_note="no"
    )
    write_alignment_labels(path, (first, decided))

    after = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert after[0] == before[0]
    moved = {key for key in after[1] if after[1][key] != before[1][key]}
    assert moved == {"owner_decision", "owner_note"}


def test_a_missing_file_holds_no_rows(tmp_path: Path) -> None:
    assert read_alignment_labels(tmp_path / "absent.jsonl") == ()


def test_rows_keep_the_order_they_were_written_in(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    rows = (repoqa_row("z_last"), repoqa_row("a_first"))

    write_alignment_labels(path, rows)

    assert [row.item_id for row in read_alignment_labels(path)] == ["z_last", "a_first"]


def test_two_rows_with_one_item_id_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="item_repoqa_1"):
        write_alignment_labels(tmp_path / "labels.jsonl", (repoqa_row(), repoqa_row()))


@pytest.mark.parametrize(
    ("edit", "named"),
    [
        (lambda row: {**row, "revision": "0.9"}, "revision '0.9'"),
        (lambda row: {**row, "stratum": "lucky"}, "'lucky'"),
        (lambda row: {**row, "alignment_pass": "pass_3"}, "'pass_3'"),
        (lambda row: {**row, "owner_decision": {"needle_identified": "maybe"}}, "'maybe'"),
        (lambda row: {**row, "ownr_note": "typo"}, "ownr_note"),
        (lambda row: {key: value for key, value in row.items() if key != "questions"}, "questions"),
    ],
)
def test_a_line_outside_the_format_is_refused_naming_its_line(
    tmp_path: Path, edit, named: str
) -> None:
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(path, (chat_row(), repoqa_row()))
    first, second = path.read_text(encoding="utf-8").splitlines()
    path.write_text(f"{first}\n{json.dumps(edit(json.loads(second)))}\n", encoding="utf-8")

    with pytest.raises(AlignmentLabelFileError) as refused:
        read_alignment_labels(path)

    assert "line 2" in str(refused.value)
    assert named in str(refused.value)


def test_a_pending_labeller_entry_round_trips_with_its_batch(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    submitted = LabellerEntry(model=OPUS_MODEL, status=LabelStatus.SUBMITTED, batch_id="b_1")

    write_alignment_labels(path, (repoqa_row(opus=submitted),))

    assert read_alignment_labels(path)[0].opus == submitted


def test_the_vendored_label_file_is_named_by_dataset_and_jev_model() -> None:
    path = vendored_alignment_label_path("repoqa-qa", "jev-1.13")

    assert path.name == "repoqa-qa__jev-1.13.jsonl"
    assert path.parent.name == "alignment"
    assert path.parent.parent.name == "data"


def test_a_label_file_set_per_dataset_replaces_the_vendored_one(tmp_path: Path) -> None:
    custom = tmp_path / "mine.jsonl"
    alignment = AlignmentConfig(label_file={"repoqa-qa": str(custom)})

    assert alignment_label_path(alignment, "repoqa-qa", "jev-1.13") == custom
    assert alignment_label_path(alignment, "swe-qa-questions", "jev-1.13") == (
        vendored_alignment_label_path("swe-qa-questions", "jev-1.13")
    )
