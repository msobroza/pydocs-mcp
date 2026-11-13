"""``write_text_atomically``: a reader sees the old file or the whole new one, never half."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pydocs_eval.atomic_text import write_text_atomically
from tests._disk_faults import FailingReplace


def test_the_text_lands_whole_in_a_new_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "rows.jsonl"

    write_text_atomically(target, "é\n")

    assert target.read_text(encoding="utf-8") == "é\n"


def test_a_second_write_replaces_the_first_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "rows.jsonl"
    write_text_atomically(target, "old\n")

    write_text_atomically(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert [path.name for path in tmp_path.iterdir()] == ["rows.jsonl"]


def test_a_failed_swap_keeps_the_old_text_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "rows.jsonl"
    write_text_atomically(target, "old\n")
    monkeypatch.setattr(os, "replace", FailingReplace())

    with pytest.raises(OSError, match="Input/output error"):
        write_text_atomically(target, "new\n")

    assert target.read_text(encoding="utf-8") == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["rows.jsonl"]


def test_a_write_that_fails_halfway_keeps_the_old_text_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "rows.jsonl"
    write_text_atomically(target, "old\n")

    with pytest.raises(UnicodeEncodeError):
        write_text_atomically(target, "new\n\ud800")  # a lone surrogate has no UTF-8 encoding

    assert target.read_text(encoding="utf-8") == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["rows.jsonl"]
