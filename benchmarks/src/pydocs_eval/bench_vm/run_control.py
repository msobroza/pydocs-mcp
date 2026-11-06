"""The owner's run-control verbs: see a run, stop it, bring its results home.

Each verb names its run by its run spec, as ``launch`` does, so no run id is
ever copied by hand (``make bench-vm-status|stop|pull RUN=runs/<name>.yaml``):

- ``status`` reads the run record, the run's live VM and the tail of the
  current attempt's synced log (``RunDirectories.child_log``, mirrored under
  ``state/``). With no run named, it lists every live bench VM by the run-id
  label across the region's zones.
- ``stop`` deletes the run's live VM, and only that. It writes nothing: the
  VM's shutdown path gives the supervisor its grace period to sync and record
  ``stopped``, so the run can be relaunched later. A run with no live VM is
  refused, naming its status.
- ``pull`` copies the run's prefix (the record, the spec copy and the synced
  state) into ``<results dir>/<run id>/``, a directory git ignores, where the
  existing reporting tools read result files. Other runs' files are never
  touched, and nothing local is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, LabelledInstance, ListedInstance
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.deployment import DeploymentNames, DeploymentNamesSource
from pydocs_eval.bench_vm.launch_plan import run_prefix_uri, run_record_uri, zone_order
from pydocs_eval.bench_vm.run_record import RunRecord, RunRecordError, parse_run_record
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile
from pydocs_eval.bench_vm.state_sync import STATE_OBJECT_PREFIX
from pydocs_eval.bench_vm.vm_request import LABEL_RUN_ID

# Under ``benchmarks/results/``, which git ignores, beside the runner's own output.
LOCAL_RUNS_RESULTS_DIR = Path("benchmarks/results/bench-vm")
# Enough of the log to see what the run is doing; read by a suffix range, so a
# log that grew for hours never downloads whole.
LOG_TAIL_MAX_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class RunControlPorts:
    """The verbs' external I/O: the cloud and ``terraform output``."""

    cloud: BenchCloud
    deployment_names: DeploymentNamesSource


class RunControlRefused(RuntimeError):
    """A verb cannot act on the run as it is: never launched, or no live VM to stop."""


@dataclass(frozen=True, slots=True)
class NamedRun:
    """A run named by its spec, and where the deployment keeps it."""

    spec_file: RunSpecFile
    names: DeploymentNames

    @property
    def run_id(self) -> str:
        return self.spec_file.run_id

    @property
    def prefix_uri(self) -> str:
        return run_prefix_uri(self.names, self.run_id)

    @property
    def make_launch_command(self) -> str:
        return f"make bench-vm RUN=runs/{self.spec_file.path.name}"

    def child_log_uri(self, attempt: int) -> str:
        """The synced copy of the child's log for ``attempt``, under the run's ``state/``."""
        dirs = RunDirectories(state_dir=Path())
        relative = dirs.child_log(attempt).as_posix()
        return self.prefix_uri + STATE_OBJECT_PREFIX + relative

    def live_vms(self, cloud: BenchCloud) -> tuple[ListedInstance, ...]:
        """The run's VMs, by its run-id label, in the zones a launch may have used."""
        zones = zone_order(self.spec_file.spec, self.names)
        return cloud.list_instances(
            self.names.project_id, zones, label=LABEL_RUN_ID, value=self.run_id
        )


def name_run(spec_file: RunSpecFile, ports: RunControlPorts) -> NamedRun:
    return NamedRun(spec_file=spec_file, names=ports.deployment_names.read_deployment_names())


def read_launched_record(cloud: BenchCloud, run: NamedRun) -> RunRecord:
    """The run's record; refused when the run was never launched or the record is unreadable."""
    uri = run_record_uri(run.names, run.run_id)
    stored = cloud.read_object(uri)
    if stored is None:
        raise RunControlRefused(
            f"run {run.run_id}: got no run record at {uri}, expected a launched run; "
            f"launch it with {run.make_launch_command}"
        )
    try:
        return parse_run_record(stored.data, source=uri)
    except RunRecordError as exc:
        raise RunControlRefused(str(exc)) from None


# --- status --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunStatusReport:
    """What ``status`` shows about one run: its record, its live VMs and its log's tail."""

    run: NamedRun
    record: RunRecord
    live: tuple[ListedInstance, ...]
    log_uri: str
    # ``None`` when no log has been synced for the current attempt yet.
    log_tail: bytes | None
    log_truncated: bool


def read_run_status(cloud: BenchCloud, run: NamedRun) -> RunStatusReport:
    record = read_launched_record(cloud, run)
    log_uri = run.child_log_uri(record.attempt)
    # One byte past the cap tells a log of exactly the cap from a longer one.
    tail = cloud.read_object_tail(log_uri, max_bytes=LOG_TAIL_MAX_BYTES + 1)
    truncated = tail is not None and len(tail) > LOG_TAIL_MAX_BYTES
    return RunStatusReport(
        run=run,
        record=record,
        live=run.live_vms(cloud),
        log_uri=log_uri,
        log_tail=tail[1:] if tail is not None and truncated else tail,
        log_truncated=truncated,
    )


def list_live_bench_vms(
    ports: RunControlPorts,
) -> tuple[tuple[str, ...], tuple[LabelledInstance, ...]]:
    """The region's zones and every VM in them carrying the run-id label."""
    names = ports.deployment_names.read_deployment_names()
    live = ports.cloud.list_labelled_instances(names.project_id, names.zones, label=LABEL_RUN_ID)
    return names.zones, live


# --- stop ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoppedRun:
    """The VMs ``stop`` deleted, and the record as it stood (``None`` when unreadable)."""

    deleted: tuple[ListedInstance, ...]
    record: RunRecord | None


def stop_run(cloud: BenchCloud, run: NamedRun) -> StoppedRun:
    """Delete every VM labelled with the run's id; refused when there is none."""
    live = run.live_vms(cloud)
    if not live:
        raise RunControlRefused(_nothing_to_stop(cloud, run))
    # Read first: it says who, if anyone, records the stop.
    record = _record_or_none(cloud, run)
    for instance in live:
        cloud.delete_instance(run.names.project_id, instance.zone, instance.name)
    return StoppedRun(deleted=live, record=record)


def _record_or_none(cloud: BenchCloud, run: NamedRun) -> RunRecord | None:
    # The record only explains what happens next; an unreadable one must not block a stop.
    try:
        return read_launched_record(cloud, run)
    except RunControlRefused:
        return None


def _nothing_to_stop(cloud: BenchCloud, run: NamedRun) -> str:
    try:
        record = read_launched_record(cloud, run)
    except RunControlRefused as exc:
        return f"run {run.run_id}: got no live VM, expected one to stop; {exc}"
    return (
        f"run {run.run_id}: got no live VM (status {record.status} at attempt "
        f"{record.attempt}), expected one to stop; nothing was deleted"
    )


# --- pull ----------------------------------------------------------------------


def pull_run(cloud: BenchCloud, run: NamedRun, results_dir: Path) -> Path:
    """Copy the run's prefix into ``results_dir/<run id>/``; refused when never launched."""
    read_launched_record(cloud, run)
    destination = results_dir / run.run_id
    cloud.download_prefix(run.prefix_uri, destination)
    return destination
