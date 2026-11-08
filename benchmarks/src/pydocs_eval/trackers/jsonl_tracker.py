"""Always-available tracker: one JSONL file per run, one JSON line per
event (spec §4.5). Zero deps — the run file is self-describing via an
``_event`` discriminator on every record.

Two modes. ``open_run`` writes one new timestamped file per leg — the
historic shape, byte-for-byte. The ``ResumableTracker`` capability
(issue #420) keeps a named run instead: ``<output_dir>/<run_id>/<leg>.jsonl``
with no timestamp, a ``run_start`` header stamped with the run id and the
leg's identity, one ``task`` event per finished task, and a ``run_resume``
event each time the file is reopened.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import TextIOBase
from pathlib import Path
from typing import IO, Literal

from ..leg_identity import LegIdentity
from ..registries import tracker_registry
from ..task_observation import TaskObservation
from .base_tracker import RecordedRun, RunHandle, validate_run_id
from .run_file_lines import RunFileLine, read_run_file_lines

log = logging.getLogger(__name__)


def _utc_ts() -> str:
    # WHY: filename-safe ISO8601 — colons in the time portion are illegal
    # on Windows filesystems and awkward in shells, so we collapse them.
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# Chars rejected by GitHub Actions artifact upload (and broadly unfriendly
# across POSIX/NTFS): " : < > | * ? \r \n. We also remap "@" → "_at_" so
# ``repoqa@v1`` reads naturally. Anything else outside the safe set
# collapses to "_" so revisions like "<TODO>" survive serialisation.
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(dataset: str) -> str:
    return _FILENAME_SAFE.sub("_", dataset.replace("@", "_at_"))


@tracker_registry.register("jsonl")
@dataclass
class JsonlExperimentTracker:
    """One file per run under ``output_dir``. Stateless w.r.t. the run —
    the open file handle rides on ``RunHandle.raw`` so concurrent runs do
    not contend on tracker state."""

    name: str = "jsonl"
    output_dir: Path = field(default_factory=lambda: Path("benchmarks/results/jsonl"))

    def open_run(
        self,
        *,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
    ) -> RunHandle:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / (f"{system}_{config_name}_{_slug(dataset)}_{_utc_ts()}.jsonl")
        fh = path.open("w", encoding="utf-8")
        _write(fh, _start_record(system, config_name, dataset, params=params, tags=tags))
        return RunHandle(tracker_name=self.name, raw=fh)

    # ── ResumableTracker capability (issue #420) ──────────────────────────

    def run_key(self, *, system: str, config_name: str, dataset_name: str) -> str:
        return f"{system}_{config_name}_{_slug(dataset_name)}"

    def recorded_run(self, *, run_id: str, run_key: str) -> RecordedRun | None:
        path = self._named_path(run_id, run_key)
        if not _has_content(path):
            return None
        return _read_named_run(path)

    def open_named_run(
        self,
        *,
        run_id: str,
        run_key: str,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
        identity: LegIdentity,
        recorded: RecordedRun | None,
    ) -> RunHandle:
        path = self._named_path(run_id, run_key)
        # WHY the caller's ``recorded``, not a second parse (#420): the resume
        # count is the run the identity check saw, read once.
        if recorded is not None:
            fh = _reopen_for_append(path)
            _write(fh, _resume_record(run_id, recorded_tasks=len(recorded.tasks)))
        else:
            _refuse_to_clear_recorded_run(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # "w" also clears a file that held nothing to resume (#420).
            fh = path.open("w", encoding="utf-8")
            header = _start_record(system, config_name, dataset, params=params, tags=tags)
            header["run_id"] = run_id
            header["identity"] = {
                "components": dict(identity.components),
                "fingerprint": identity.fingerprint,
            }
            _write(fh, header)
        return RunHandle(tracker_name=self.name, raw=fh)

    def record_task(self, handle: RunHandle, observation: TaskObservation, *, step: int) -> None:
        _write(_file(handle), {"_event": "task", **observation.to_record(step=step)})

    def _named_path(self, run_id: str, run_key: str) -> Path:
        validate_run_id(run_id)
        return self.output_dir / run_id / f"{run_key}.jsonl"

    def log_metric(
        self,
        handle: RunHandle,
        name: str,
        value: float,
        step: int | None = None,
    ) -> None:
        _write(
            _file(handle),
            {"_event": "metric", "name": name, "value": value, "step": step},
        )

    def log_artifact(
        self,
        handle: RunHandle,
        path: Path,
        name: str | None = None,
    ) -> None:
        _write(
            _file(handle),
            {
                "_event": "artifact",
                "name": name or path.name,
                "path": str(path),
            },
        )

    def close_run(
        self,
        handle: RunHandle,
        status: Literal["finished", "failed"],
    ) -> None:
        fh = _file(handle)
        # WHY: the runner's try/finally path may call close twice on
        # failure; a second close on an already-closed handle would raise
        # ValueError, masking the original exception.
        if fh.closed:
            return
        _write(fh, {"_event": "run_end", "status": status})
        fh.close()


def _start_record(
    system: str,
    config_name: str,
    dataset: str,
    *,
    params: Mapping[str, str],
    tags: Mapping[str, str],
) -> dict[str, object]:
    return {
        "_event": "run_start",
        "system": system,
        "config_name": config_name,
        "dataset": dataset,
        "params": dict(params),
        "tags": dict(tags),
        "ts": datetime.now(UTC).isoformat(),
    }


def _resume_record(run_id: str, *, recorded_tasks: int) -> dict[str, object]:
    return {
        "_event": "run_resume",
        "run_id": run_id,
        "recorded_tasks": recorded_tasks,
        "ts": datetime.now(UTC).isoformat(),
    }


def _has_content(path: Path) -> bool:
    # WHY size, not existence: a process killed before its header reached the
    # disk leaves an empty file, which holds nothing to resume.
    return path.is_file() and path.stat().st_size > 0


def _reopen_for_append(path: Path) -> IO[str]:
    """Open for append so the next record starts on a line of its own.

    A process killed mid-write leaves a truncated last line with no newline;
    without one, the first appended record would be glued onto that fragment
    and lost with it.
    """
    with path.open("rb") as raw:
        raw.seek(-1, 2)
        ends_with_newline = raw.read(1) == b"\n"
    fh = path.open("a", encoding="utf-8")
    if not ends_with_newline:
        fh.write("\n")
    return fh


def _read_named_run(path: Path) -> RecordedRun | None:
    """Parse a named run's header identity and task records.

    A corrupt line — a truncated last line from a killed run, or such a
    fragment left mid-file by an earlier resume — is skipped with a warning,
    the corrupt-line policy of the campaign and agent-track ledgers; the task
    it described simply runs again.

    A file with neither a header nor a task record — a kill, or a state sync,
    that caught the header mid-write — holds nothing to resume, so it reads
    as no run at all (``None``) and the leg starts afresh (#420). Task
    records without a header still refuse: they cannot be checked against
    the leg's identity.
    """
    header: Mapping[str, object] | None = None
    tasks: dict[str, TaskObservation] = {}
    for line in read_run_file_lines(path):
        if line.decode_error is not None:
            _warn_corrupt_line_skipped(path, line)
        record = line.record
        if record is None:
            continue
        if record.get("_event") == "run_start" and header is None:
            header = record
        elif record.get("_event") == "task":
            observation = TaskObservation.from_record(record)
            tasks[observation.task_id] = observation
    if header is None and not tasks:
        _warn_headerless_run_treated_as_empty(path)
        return None
    return RecordedRun(identity=_header_identity(path, header), tasks=tasks)


def _refuse_to_clear_recorded_run(path: Path) -> None:
    """Fail loud rather than truncate a header or task records ``recorded=None`` did not account for.

    Reached only when the file has content, which on the sweep's path means
    a header cut short with no task after it, so the normal reopen still parses the file once.
    """
    if not _has_content(path):
        return
    events = {line.record.get("_event") for line in read_run_file_lines(path) if line.record}
    if events & {"run_start", "task"}:
        raise ValueError(
            f"named run {path} holds a header or task records, but open_named_run got "
            "recorded=None; expected the RecordedRun that recorded_run returned for it"
        )


def _warn_headerless_run_treated_as_empty(path: Path) -> None:
    log.warning(
        json.dumps(
            {
                "event": "jsonl_named_run_without_header_treated_as_empty",
                "path": str(path),
                "discarded_bytes": path.stat().st_size,
            }
        )
    )


def _warn_corrupt_line_skipped(path: Path, line: RunFileLine) -> None:
    log.warning(
        json.dumps(
            {
                "event": "jsonl_run_corrupt_line_skipped",
                "path": str(path),
                "line": line.number,
                "error": line.decode_error,
            }
        )
    )


def _header_identity(path: Path, header: Mapping[str, object] | None) -> LegIdentity:
    identity = header.get("identity") if header is not None else None
    if not isinstance(identity, Mapping):
        raise ValueError(
            f"named run {path} has no run_start header with an identity; expected the "
            "first record to be the run_start a run-id sweep writes. Move the file "
            "aside or pick a new run id."
        )
    components, fingerprint = identity.get("components"), identity.get("fingerprint")
    if not isinstance(components, Mapping) or not isinstance(fingerprint, str):
        raise ValueError(
            f"named run {path} has a malformed identity: got {identity!r}, expected "
            '{"components": {...}, "fingerprint": "<sha256>"}'
        )
    return LegIdentity(components=dict(components), fingerprint=fingerprint)


def _file(handle: RunHandle) -> IO[str]:
    raw = handle.raw
    if not isinstance(raw, TextIOBase):
        raise TypeError(
            f"JsonlExperimentTracker expected a text file handle, got {type(raw).__name__}"
        )
    return raw


def _write(fh: IO[str], record: Mapping[str, object]) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()  # Performance: tail-followability matters more than throughput here.
