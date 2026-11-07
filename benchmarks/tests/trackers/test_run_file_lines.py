"""Pin ``read_run_file_lines``: the one parser of a JSONL run file's lines (finding F13).

The JSONL tracker and ``ci_compare`` read the same run files under different
corruption policies; each builds its policy on these per-line facts.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.trackers.run_file_lines import read_run_file_lines


def test_each_line_says_whether_it_is_a_record_blank_or_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text('{"_event": "run_start"}\n\n[1, 2]\n{"_event": "ta', encoding="utf-8")

    lines = read_run_file_lines(path)

    assert [line.number for line in lines] == [1, 2, 3, 4]
    assert lines[0].record == {"_event": "run_start"}
    assert lines[0].decode_error is None
    # A blank line and a JSON value that is not an object carry no record,
    # but only a line that is not JSON at all carries a decode error.
    assert (lines[1].record, lines[1].decode_error) == (None, None)
    assert (lines[2].record, lines[2].decode_error) == (None, None)
    assert lines[3].record is None
    assert lines[3].decode_error is not None
