"""The run record: the one versioned contract between the laptop's launcher and the VM's supervisor."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.run_record import (
    RUN_RECORD_SCHEMA_VERSION,
    RunRecord,
    RunRecordError,
    RunStatus,
    RunTransition,
    parse_run_record,
    run_record_bytes,
)

_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def _launching_record() -> RunRecord:
    transition = RunTransition(
        status=RunStatus.LAUNCHING,
        at=_AT,
        attempt=1,
        instance="bench-cpu-smoke-0123abcd-1",
        zone=None,
        machine="n2-standard-8",
        detail="zones to try: europe-west4-a",
    )
    return RunRecord(
        schema_version=RUN_RECORD_SCHEMA_VERSION,
        run_id="cpu-smoke-0123abcd",
        spec_digest="sha256:" + "0" * 64,
        kind=RunKind.RETRIEVAL,
        git_ref="main",
        commit="a" * 40,
        image="europe-west4-docker.pkg.dev/p/bench/bench-cpu:" + "a" * 40,
        image_digest="sha256:" + "b" * 64,
        status=RunStatus.LAUNCHING,
        attempt=1,
        history=(transition,),
    )


def test_run_status_has_exactly_the_eight_lifecycle_values() -> None:
    assert [status.value for status in RunStatus] == [
        "launching",
        "running",
        "succeeded",
        "failed",
        "timed_out",
        "preempted",
        "stopped",
        "lost",
    ]


def test_a_record_round_trips_through_its_json_bytes() -> None:
    record = _launching_record()

    assert parse_run_record(run_record_bytes(record), source="run.json") == record


def test_the_json_carries_every_contract_field() -> None:
    payload = json.loads(run_record_bytes(_launching_record()))

    assert set(payload) == {
        "schema_version",
        "run_id",
        "spec_digest",
        "kind",
        "git_ref",
        "commit",
        "baseline_ref",
        "baseline_commit",
        "candidate_ref",
        "candidate_commit",
        "image",
        "image_digest",
        "status",
        "attempt",
        "history",
        "last_sync_at",
        "exit_code",
        "cache_bytes_restored",
        "cache_bytes_pushed",
    }
    assert set(payload["history"][0]) == {
        "status",
        "at",
        "attempt",
        "instance",
        "zone",
        "machine",
        "detail",
    }
    assert payload["last_sync_at"] is None
    assert payload["exit_code"] is None
    assert payload["cache_bytes_restored"] == payload["cache_bytes_pushed"] == 0


def test_a_transition_sets_the_status_and_attempt_and_appends_to_history() -> None:
    record = _launching_record()
    failed = RunTransition(
        status=RunStatus.FAILED,
        at=_AT,
        attempt=1,
        instance=None,
        zone=None,
        machine=None,
        detail="boom",
    )

    after = record.with_transition(failed)

    assert after.status is RunStatus.FAILED
    assert after.history == (*record.history, failed)
    assert record.status is RunStatus.LAUNCHING


def test_an_unknown_schema_version_is_refused_naming_both_versions() -> None:
    payload = json.loads(run_record_bytes(_launching_record()))
    payload["schema_version"] = RUN_RECORD_SCHEMA_VERSION + 1

    with pytest.raises(RunRecordError) as raised:
        parse_run_record(json.dumps(payload).encode(), source="gs://runs/r/run.json")

    message = str(raised.value)
    assert "gs://runs/r/run.json" in message
    assert f"got {RUN_RECORD_SCHEMA_VERSION + 1}" in message
    assert f"expected {RUN_RECORD_SCHEMA_VERSION}" in message


def test_a_record_that_is_not_json_is_refused_naming_the_source() -> None:
    with pytest.raises(RunRecordError, match="gs://runs/r/run.json"):
        parse_run_record(b"not json", source="gs://runs/r/run.json")


def test_an_unknown_status_is_refused() -> None:
    payload = json.loads(run_record_bytes(_launching_record()))
    payload["status"] = "exploded"

    with pytest.raises(RunRecordError, match="status"):
        parse_run_record(json.dumps(payload).encode(), source="run.json")
