"""The reference-answer rows file: one JSON row per task, written once, read back whole.

The repoqa-qa references live in a versioned file keyed by task id (judge 9c):
each row carries the text, the writer's model id, the prompt hash, why the
fallback wrote it (empty when the primary did) and the file format's revision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import ReferenceAnswer
from pydocs_eval.datasets.reference_answers import (
    ReferenceRow,
    ReferenceRowsError,
    read_reference_rows,
    reference_answers_by_task,
    write_reference_rows,
)

_OPUS = ReferenceRow(
    "repoqa-qa/repo_qa/b",
    ReferenceAnswer("`b.py` — `beta`", "anthropic/claude-opus-5.5-20260921", "9f" * 32),
)
_SONNET = ReferenceRow(
    "repoqa-qa/repo_qa/a",
    ReferenceAnswer("`a.py` — `alpha`", "anthropic/claude-sonnet-5-20260630", "3c" * 32),
    fallback_reason="batch b1 ended expired",
)


def test_rows_round_trip_sorted_by_task_id(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"

    write_reference_rows(path, [_OPUS, _SONNET])

    assert read_reference_rows(path) == (_SONNET, _OPUS)


def test_each_line_is_canonical_json_carrying_the_revision(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"

    write_reference_rows(path, [_SONNET])

    (line,) = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert list(row) == sorted(row)
    assert row == {
        "fallback_reason": "batch b1 ended expired",
        "model_id": "anthropic/claude-sonnet-5-20260630",
        "prompt_hash": "3c" * 32,
        "revision": "1.0",
        "task_id": "repoqa-qa/repo_qa/a",
        "text": "`a.py` — `alpha`",
    }


def test_new_rows_join_the_stored_ones(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"
    write_reference_rows(path, [_OPUS])

    write_reference_rows(path, [_SONNET])

    assert read_reference_rows(path) == (_SONNET, _OPUS)


def test_a_stored_reference_is_never_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"
    write_reference_rows(path, [_OPUS])
    changed = ReferenceRow(_OPUS.task_id, ReferenceAnswer("other", "m", "h"))

    with pytest.raises(ReferenceRowsError, match="repoqa-qa/repo_qa/b"):
        write_reference_rows(path, [changed])

    assert read_reference_rows(path) == (_OPUS,)


def test_writing_the_same_row_again_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"
    write_reference_rows(path, [_OPUS])
    before = path.read_bytes()

    write_reference_rows(path, [_OPUS])

    assert path.read_bytes() == before


def test_a_missing_file_holds_no_row(tmp_path: Path) -> None:
    assert read_reference_rows(tmp_path / "absent.jsonl") == ()


@pytest.mark.parametrize(
    ("line", "named"),
    [
        (
            '{"task_id": "t", "revision": "2.0", "text": "x", "model_id": "m", '
            '"prompt_hash": "h", "fallback_reason": ""}',
            "revision",
        ),
        ('{"task_id": "t", "revision": "1.0"}', "text"),
        ("not json", "line 1"),
    ],
)
def test_a_malformed_row_is_refused_naming_it(tmp_path: Path, line: str, named: str) -> None:
    path = tmp_path / "refs.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(ReferenceRowsError, match=named):
        read_reference_rows(path)


def test_the_answers_are_read_by_task_id(tmp_path: Path) -> None:
    path = tmp_path / "refs.jsonl"
    write_reference_rows(path, [_OPUS, _SONNET])

    by_task = reference_answers_by_task(path)

    assert by_task == {_OPUS.task_id: _OPUS.reference, _SONNET.task_id: _SONNET.reference}
