"""The run record: ``gs://<runs-bucket>/<run-id>/run.json``, one per run, across every attempt.

The laptop's launcher and the VM's supervisor can come from different commits;
this record is the one contract that crosses that line, so it carries a schema
version both sides check, and a reader refuses a version it does not know
instead of guessing at the fields.

Status ownership: the launcher writes ``launching`` (and ``failed`` when its
own launch fails, ``lost`` when it finds a run whose VM vanished); the
supervisor writes ``running`` and every ending. Every write goes through a GCS
generation precondition (``BenchCloud.write_object``), so a stale writer fails
instead of overwriting a newer status.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pydocs_eval.bench_vm.run_kinds import RunKind

# Bump on any change a reader of the other side could misread; both the
# launcher and the supervisor refuse a version they do not know.
RUN_RECORD_SCHEMA_VERSION = 1
RUN_RECORD_OBJECT_NAME = "run.json"
SPEC_COPY_OBJECT_NAME = "spec.yaml"


class RunStatus(StrEnum):
    """Where a run is in its lifecycle; the record's ``status`` and every transition's."""

    LAUNCHING = "launching"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PREEMPTED = "preempted"
    STOPPED = "stopped"
    LOST = "lost"


_RECORD_MODEL = ConfigDict(extra="forbid", frozen=True)


class RunTransition(BaseModel):
    """One entry of the history: who moved the run to ``status``, where, and why."""

    model_config = _RECORD_MODEL

    status: RunStatus
    at: datetime
    attempt: int = Field(ge=1)
    instance: str | None
    zone: str | None
    machine: str | None
    detail: str = ""


class RunRecord(BaseModel):
    """The state of one run: its frozen inputs, its image, its status and its full history."""

    model_config = _RECORD_MODEL

    schema_version: int
    run_id: str
    # sha256 of the verbatim spec file uploaded next to the record.
    spec_digest: str
    kind: RunKind
    git_ref: str
    commit: str
    baseline_ref: str | None = None
    baseline_commit: str | None = None
    candidate_ref: str | None = None
    candidate_commit: str | None = None
    image: str
    image_digest: str
    status: RunStatus
    attempt: int = Field(ge=1)
    history: tuple[RunTransition, ...]
    last_sync_at: datetime | None = None
    exit_code: int | None = None
    cache_bytes_restored: int = Field(default=0, ge=0)
    cache_bytes_pushed: int = Field(default=0, ge=0)

    def with_transition(self, transition: RunTransition) -> RunRecord:
        """The record after ``transition``: its status and attempt, appended to the history."""
        return self.model_copy(
            update={
                "status": transition.status,
                "attempt": transition.attempt,
                "history": (*self.history, transition),
            }
        )


class RunRecordError(ValueError):
    """A run record this code cannot read: unknown schema version, bad JSON or bad fields."""


def run_record_bytes(record: RunRecord) -> bytes:
    """The record as the UTF-8 JSON object stored in ``run.json``."""
    return (record.model_dump_json(indent=2) + "\n").encode("utf-8")


def parse_run_record(data: bytes, *, source: str) -> RunRecord:
    """Read a ``run.json`` from ``source``, refusing a schema version other than this code's."""
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunRecordError(
            f"{source}: got unreadable run record ({exc}), expected a JSON object"
        ) from None
    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if version != RUN_RECORD_SCHEMA_VERSION:
        raise RunRecordError(
            f"{source}: schema_version: got {version!r}, expected {RUN_RECORD_SCHEMA_VERSION} "
            "(the launcher and the supervisor must agree on the run record's schema)"
        )
    try:
        return RunRecord.model_validate(payload)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise RunRecordError(f"{source}: got an invalid run record ({fields})") from None
