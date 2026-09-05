"""Phase 2 Task 1 — JSONL trace writer + blob store (ADR 0009/0010).

Covers the header stamp, the trajectory_id-reuse hard error (the structural
guard against two rollouts interleaving one file), and the write-once
content-addressed blob convention (``blobs/<sha256-hex>``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pydocs_mcp.observability.trace_writer import (
    SERVER_EVENTS_FILENAME,
    TraceJsonlWriter,
    TrajectoryIdReuseError,
    write_result_blob,
)


def _events_path(tmp_path: Path) -> Path:
    return tmp_path / "traj-1" / SERVER_EVENTS_FILENAME


class TestTraceJsonlWriter:
    def test_append_writes_one_json_line_and_flushes(self, tmp_path: Path) -> None:
        writer = TraceJsonlWriter(_events_path(tmp_path))
        writer.open_with_reuse_guard()
        try:
            writer.append({"_event": "trace_header", "trajectory_id": "traj-1"})
            writer.append({"_event": "tool_call", "seq": 1})
        finally:
            writer.close()
        lines = _events_path(tmp_path).read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["_event"] for line in lines] == ["trace_header", "tool_call"]

    def test_reopen_after_header_is_id_reuse_hard_error(self, tmp_path: Path) -> None:
        writer = TraceJsonlWriter(_events_path(tmp_path))
        writer.open_with_reuse_guard()
        writer.append({"_event": "trace_header", "trajectory_id": "traj-1"})
        writer.close()
        second = TraceJsonlWriter(_events_path(tmp_path))
        with pytest.raises(TrajectoryIdReuseError, match="traj-1"):
            second.open_with_reuse_guard()

    def test_open_on_existing_empty_file_is_fresh(self, tmp_path: Path) -> None:
        """A crash before the header left an empty file — not a reuse."""
        path = _events_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.touch()
        writer = TraceJsonlWriter(path)
        writer.open_with_reuse_guard()
        writer.close()

    def test_open_on_non_header_content_raises(self, tmp_path: Path) -> None:
        path = _events_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("not-json garbage\n", encoding="utf-8")
        with pytest.raises(TrajectoryIdReuseError, match="server_events"):
            TraceJsonlWriter(path).open_with_reuse_guard()

    def test_append_before_open_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="not open"):
            TraceJsonlWriter(_events_path(tmp_path)).append({"a": 1})

    def test_append_is_deterministic_key_order(self, tmp_path: Path) -> None:
        """Canonical serialization (sorted keys) so identical event dicts
        produce identical bytes regardless of insertion order (R6)."""
        writer = TraceJsonlWriter(_events_path(tmp_path))
        writer.open_with_reuse_guard()
        writer.append({"b": 2, "_event": "x", "a": 1})
        writer.close()
        line = _events_path(tmp_path).read_text(encoding="utf-8").strip()
        assert line == '{"_event":"x","a":1,"b":2}'


class TestBlobStore:
    def test_blob_name_is_sha256_of_content(self, tmp_path: Path) -> None:
        data = b'{"text": "hello"}'
        digest = write_result_blob(tmp_path / "blobs", data)
        assert digest == hashlib.sha256(data).hexdigest()
        assert (tmp_path / "blobs" / digest).read_bytes() == data

    def test_blob_write_is_idempotent(self, tmp_path: Path) -> None:
        data = b"repeated read of the same file"
        first = write_result_blob(tmp_path / "blobs", data)
        second = write_result_blob(tmp_path / "blobs", data)
        assert first == second
        assert len(list((tmp_path / "blobs").iterdir())) == 1
