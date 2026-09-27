"""``trace_recorded``: the one "has an id AND its events file on disk" rule.

The campaign arm books a run as complete by it, and the timeout wrapper decides by it
whether a killed run kept its trace — one spelling, so the two cannot disagree.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME, trace_recorded


def test_a_run_with_an_id_and_its_events_file_is_recorded(tmp_path: Path) -> None:
    (tmp_path / SERVER_EVENTS_FILENAME).write_text("{}\n", encoding="utf-8")

    assert trace_recorded("t1", tmp_path) is True


def test_a_run_without_an_id_is_not_recorded_whatever_is_on_disk(tmp_path: Path) -> None:
    (tmp_path / SERVER_EVENTS_FILENAME).write_text("{}\n", encoding="utf-8")

    assert trace_recorded("", tmp_path) is False


def test_only_the_events_file_counts_never_the_directory(tmp_path: Path) -> None:
    """``Path()`` — a traceless run's directory — is the working directory, which always
    exists; a directory is never evidence of a trace."""
    assert trace_recorded("t1", tmp_path) is False
    (tmp_path / SERVER_EVENTS_FILENAME).mkdir()
    assert trace_recorded("t1", tmp_path) is False
