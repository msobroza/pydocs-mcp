"""Append-only JSONL trace file + content-addressed blob store (ADR 0010).

The raw server-side capture substrate: one ``server_events.jsonl`` per
trajectory (header first line, then event lines) and one shared
``blobs/<sha256-hex>`` store per run directory. The format is the contract —
the eval-side persister implements the same convention independently, with
zero import coupling across the packaging boundary (ADR 0009 placement).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from pydocs_mcp.exceptions import PydocsMCPError

TRACE_SCHEMA_VERSION = 1
# The server-side raw capture file, distinct from the eval-side canonical
# merged ``events.jsonl`` (produced later from BOTH captures — ADR 0010).
SERVER_EVENTS_FILENAME = "server_events.jsonl"
TRACE_HEADER_EVENT = "trace_header"


class TrajectoryIdReuseError(PydocsMCPError, RuntimeError):
    """Two rollouts sharing one trajectory_id would silently interleave one
    file, violating R1's append-only-per-trajectory semantics (ADR 0009)."""


def canonical_trace_json(payload: Mapping[str, object]) -> str:
    """Canonical one-line JSON: sorted keys, no spaces, non-ASCII preserved.

    Sorted keys make identical event dicts byte-identical regardless of
    insertion order (R6 determinism); ``default=str`` keeps capture from
    ever crashing a live tool call on an exotic arg value.

    Example:
        >>> canonical_trace_json({"b": 1, "_event": "x"})
        '{"_event":"x","b":1}'
    """
    return json.dumps(
        dict(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def write_result_blob(blobs_dir: Path, data: bytes) -> str:
    """Write ``data`` to ``blobs_dir/<sha256-hex>`` and return the hex digest.

    Write-once by hash: a name collision IS a content match (content
    addressing), so repeated reads dedupe to one blob and capture retries
    are idempotent (R6).

    Example:
        >>> write_result_blob(Path("/tmp/run/blobs"), b"x")  # doctest: +SKIP
        '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881'
    """
    digest = hashlib.sha256(data).hexdigest()
    blob_path = blobs_dir / digest
    if not blob_path.exists():
        blobs_dir.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(data)
    return digest


class TraceJsonlWriter:
    """Held-handle append-only JSONL writer with the id-reuse open guard.

    The handle stays open for the process lifetime (25.3 µs/call measured vs
    255.1 µs with per-call open/close — module docstring of the package);
    every ``append`` flushes so a crash loses at most the in-flight line.
    """

    def __init__(self, events_path: Path) -> None:
        self._events_path = events_path
        self._fh: TextIO | None = None

    def open_with_reuse_guard(self) -> None:
        """Open for append; hard-error if the file already carries content.

        A file with a header means another process already opened this
        trajectory_id (reuse); non-header content means a corrupt or foreign
        file — both are unanalyzable, so both fail loudly (ADR 0009).
        """
        self._raise_if_already_started()
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._events_path.open("a", encoding="utf-8")

    def _raise_if_already_started(self) -> None:
        if not self._events_path.exists():
            return
        with self._events_path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
        if not first_line:
            return  # crash before the header wrote — a fresh open is safe
        raise TrajectoryIdReuseError(
            f"trace file {self._events_path} already has content"
            f" (first line: {first_line[:120]!r}); trajectory_id reuse is a hard"
            " error — every rollout must mint a fresh UUID (ADR 0009)"
        )

    def append(self, payload: Mapping[str, object]) -> None:
        if self._fh is None:
            raise RuntimeError(
                f"trace writer for {self._events_path} is not open;"
                " call open_with_reuse_guard() before append()"
            )
        self._fh.write(canonical_trace_json(payload) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
