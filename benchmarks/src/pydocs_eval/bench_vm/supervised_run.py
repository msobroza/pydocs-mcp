"""Load the run a bench VM serves: its host facts, its run record and the verbatim spec copy.

Everything here happens before the supervisor writes anything or starts a
child, so each mismatch is a ``SupervisorRefused``:

- a record whose schema version this code does not know, named with both
  versions (the launcher and the supervisor can come from different commits);
- a record for another run or attempt, or one already past ``launching``,
  which another supervisor owns or which has already ended;
- a spec copy whose digest or run id disagrees with the record.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.host_facts import HostFacts, read_host_facts
from pydocs_eval.bench_vm.launch_plan import spec_digest
from pydocs_eval.bench_vm.run_record import (
    RUN_RECORD_OBJECT_NAME,
    SPEC_COPY_OBJECT_NAME,
    RunRecord,
    RunRecordError,
    RunStatus,
    parse_run_record,
)
from pydocs_eval.bench_vm.run_record_keeper import RunRecordKeeper
from pydocs_eval.bench_vm.run_spec import RunSpec, RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.supervisor_refusal import SupervisorRefused


@dataclass(frozen=True, slots=True)
class SupervisedRun:
    """The run as loaded: where this VM runs, the run's spec, and the record to write to."""

    facts: HostFacts
    spec: RunSpec
    keeper: RunRecordKeeper

    @property
    def record(self) -> RunRecord:
        return self.keeper.record


def load_supervised_run(cloud: BenchCloud) -> SupervisedRun:
    """Read and cross-check the host facts, the run record and the spec copy."""
    facts = read_host_facts(cloud)
    keeper = _read_record(cloud, facts)
    spec = _read_spec_copy(cloud, facts, keeper.record)
    return SupervisedRun(facts=facts, spec=spec, keeper=keeper)


def _read_record(cloud: BenchCloud, facts: HostFacts) -> RunRecordKeeper:
    uri = facts.run_prefix_uri + RUN_RECORD_OBJECT_NAME
    stored = cloud.read_object(uri)
    if stored is None:
        raise SupervisorRefused(f"{uri}: got no run record, expected the one the launcher wrote")
    try:
        record = parse_run_record(stored.data, source=uri)
    except RunRecordError as exc:
        raise SupervisorRefused(str(exc)) from None
    _check_record_is_this_attempt(uri, record, facts)
    return RunRecordKeeper(cloud=cloud, uri=uri, record=record, generation=stored.generation)


def _check_record_is_this_attempt(uri: str, record: RunRecord, facts: HostFacts) -> None:
    found = (record.run_id, record.attempt)
    if found != (facts.run_id, facts.attempt):
        raise SupervisorRefused(
            f"{uri}: got run {record.run_id} attempt {record.attempt}, expected run "
            f"{facts.run_id} attempt {facts.attempt} as this VM's metadata says"
        )
    if record.status is not RunStatus.LAUNCHING:
        raise SupervisorRefused(
            f"{uri}: got status {record.status} at attempt {record.attempt}, expected "
            f"{RunStatus.LAUNCHING}; this attempt was already supervised"
        )


def _read_spec_copy(cloud: BenchCloud, facts: HostFacts, record: RunRecord) -> RunSpec:
    uri = facts.run_prefix_uri + SPEC_COPY_OBJECT_NAME
    stored = cloud.read_object(uri)
    if stored is None:
        raise SupervisorRefused(f"{uri}: got no spec copy, expected the one the launcher wrote")
    digest = spec_digest(stored.data)
    if digest != record.spec_digest:
        raise SupervisorRefused(
            f"{uri}: got digest {digest}, expected the record's spec_digest {record.spec_digest}"
        )
    return _validated_spec(uri, stored.data, record.run_id)


def _validated_spec(uri: str, data: bytes, run_id: str) -> RunSpec:
    # The loader derives the run id from the file stem, so the copy is checked
    # under the stem it was launched as: the run id less its "-<8 hex>" suffix.
    stem = run_id.rsplit("-", 1)[0]
    with tempfile.TemporaryDirectory(prefix="bench-vm-spec-") as scratch:
        path = Path(scratch) / f"{stem}.yaml"
        path.write_bytes(data)
        try:
            loaded = load_run_spec(path)
        except RunSpecError as exc:
            raise SupervisorRefused(f"{uri}: got a spec copy this code refuses: {exc}") from None
    if loaded.run_id != run_id:
        raise SupervisorRefused(f"{uri}: got a spec for run {loaded.run_id}, expected {run_id}")
    return loaded.spec
