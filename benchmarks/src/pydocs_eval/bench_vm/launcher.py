"""The launcher: ``make bench-vm RUN=runs/<name>.yaml`` launches the first attempt of a run.

In order (spec "Launcher"): load the spec; read the deployment's names from
``terraform output``; compute the run id and read the run record; resolve and
freeze the commits; check the configs and ``corpus_dir`` at the commit; check
the machine type through the Compute API; look up the image; write the run
record and the verbatim spec copy; create the VM, moving to the next zone on a
stockout only. Everything that can fail on the laptop fails before anything is
written or created, and once the record exists, any failure is recorded as
``failed`` rather than leaving a ``launching`` run with no VM.

Two cases are refused by name until their own changes land: a launch over an
existing run record (relaunch) and a missing image tag (build on demand).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, ZoneStockoutError
from pydocs_eval.bench_vm.deployment import DeploymentNames, DeploymentNamesSource
from pydocs_eval.bench_vm.launch_plan import (
    LaptopChecks,
    LaunchPlan,
    PlanInputs,
    build_launch_plan,
    run_laptop_checks,
    run_record_uri,
    zone_order,
)
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.machine_check import check_machine_type, refuse_unoffered_machine_type
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.run_record import (
    RunRecordError,
    RunStatus,
    RunTransition,
    parse_run_record,
    run_record_bytes,
)
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile
from pydocs_eval.bench_vm.vm_request import GpuAttachment, image_tag_name

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LaunchPorts:
    """The launcher's external I/O: the cloud, ``terraform output``, the clock."""

    cloud: BenchCloud
    deployment_names: DeploymentNamesSource
    clock: Callable[[], datetime]


class LaunchFailed(RuntimeError):
    """The launch failed after the run record was written; the record says ``failed``."""


def log_launch_event(event: str, **fields: object) -> None:
    log.info(json.dumps({"event": event, **fields}, default=str))


def launch_first_run(
    spec_file: RunSpecFile, spec_bytes: bytes, git: GitCheckout, ports: LaunchPorts
) -> LaunchPlan:
    """Launch attempt 1 of ``spec_file``'s run and return the plan it executed."""
    names = ports.deployment_names.read_deployment_names()
    _refuse_existing_record(ports.cloud, run_record_uri(names, spec_file.run_id))
    checks = run_laptop_checks(git, spec_file.spec)
    zones, gpu = _zones_offering_the_machine(ports.cloud, names, spec_file)
    inputs = PlanInputs(
        names=names, zones=zones, image_digest=_image_digest(ports.cloud, names, checks), gpu=gpu
    )
    plan = build_launch_plan(spec_file, spec_bytes, checks, inputs, ports.clock)
    generation = _write_launching_record(ports.cloud, plan)
    try:
        _copy_spec(ports.cloud, plan)
        _create_vm(ports, plan, generation)
    except LaunchFailed:
        raise
    except Exception as exc:  # whatever broke, never leave `launching` without a VM
        _record_failed(ports, plan, generation, zone=None, detail=f"{type(exc).__name__}: {exc}")
    return plan


def _refuse_existing_record(cloud: BenchCloud, uri: str) -> None:
    stored = cloud.read_object(uri)
    if stored is None:
        return
    try:
        record = parse_run_record(stored.data, source=uri)
    except RunRecordError as exc:
        raise LaunchRefused(f"existing run record at {uri} is unreadable: {exc}") from None
    raise LaunchRefused(
        f"existing run record at {uri}: got status {record.status} at attempt {record.attempt}, "
        "expected no record for a first launch; relaunching an existing run is not supported "
        "yet (it lands with the relaunch change)"
    )


def _zones_offering_the_machine(
    cloud: BenchCloud, names: DeploymentNames, spec_file: RunSpecFile
) -> tuple[tuple[str, ...], GpuAttachment | None]:
    """The launch order less zones without the machine type, and how its GPU attaches.

    A zone that lacks the type could never fit the VM, so it is dropped here,
    before anything is written, rather than failing the launch at create time.
    """
    spec = spec_file.spec
    machine_type = spec.resolved_machine_type
    order = zone_order(spec, names)
    offered = {
        zone: cloud.describe_machine_type(names.project_id, zone, machine_type) for zone in order
    }
    zones = tuple(zone for zone in order if offered[zone] is not None)
    for zone in order:
        if offered[zone] is None:
            log_launch_event("bench_vm_zone_skipped", zone=zone, machine_type=machine_type)
    first = offered[zones[0]] if zones else None
    if first is None:
        raise refuse_unoffered_machine_type(machine_type, order)
    return zones, check_machine_type(first, spec.gpu)


def _image_digest(cloud: BenchCloud, names: DeploymentNames, checks: LaptopChecks) -> str:
    image = image_tag_name(names.image_repository, checks.flavour, checks.commits.run)
    digest = cloud.find_image_digest(image)
    if digest is None:
        raise LaunchRefused(
            f"missing image tag: got no {image} in Artifact Registry, expected the run commit's "
            f"{checks.flavour} image; building it on demand is not supported yet (it lands with "
            "the build-on-demand change)"
        )
    return digest


def _write_launching_record(cloud: BenchCloud, plan: LaunchPlan) -> int:
    """Claim the run: the record is written at generation 0, so it must not exist yet."""
    try:
        generation = cloud.write_object(
            plan.record_uri, run_record_bytes(plan.record), if_generation_match=0
        )
    except BenchCloudError as exc:
        raise LaunchFailed(f"writing the run record failed: {exc}") from None
    log_launch_event("bench_vm_record_written", run_id=plan.run_id, status=RunStatus.LAUNCHING)
    return generation


def _copy_spec(cloud: BenchCloud, plan: LaunchPlan) -> None:
    """Upload the verbatim spec once, at the first launch, next to the record."""
    cloud.write_object(plan.spec_copy_uri, plan.spec_bytes, if_generation_match=0)


def _create_vm(ports: LaunchPorts, plan: LaunchPlan, generation: int) -> None:
    stockouts: list[str] = []
    for zone in plan.zones:
        try:
            ports.cloud.create_instance(plan.request_in(zone))
        except ZoneStockoutError as exc:
            stockouts.append(zone)
            log_launch_event(
                "bench_vm_zone_stockout", run_id=plan.run_id, zone=zone, detail=exc.detail
            )
            continue
        except BenchCloudError as exc:
            _record_failed(ports, plan, generation, zone=zone, detail=str(exc))
        _log_created(plan, zone)
        return
    detail = f"no capacity in any zone tried: {', '.join(stockouts)}"
    _record_failed(ports, plan, generation, zone=None, detail=detail)


def _log_created(plan: LaunchPlan, zone: str) -> None:
    log_launch_event(
        "bench_vm_instance_created",
        run_id=plan.run_id,
        instance=plan.vm_request.name,
        zone=zone,
        attempt=plan.record.attempt,
    )


def _record_failed(
    ports: LaunchPorts, plan: LaunchPlan, generation: int, *, zone: str | None, detail: str
) -> None:
    """Record ``failed`` over the launcher's own write, then raise ``LaunchFailed``."""
    transition = RunTransition(
        status=RunStatus.FAILED,
        at=ports.clock(),
        attempt=plan.record.attempt,
        instance=plan.vm_request.name,
        zone=zone,
        machine=plan.vm_request.machine_type,
        detail=detail,
    )
    failed = run_record_bytes(plan.record.with_transition(transition))
    try:
        ports.cloud.write_object(plan.record_uri, failed, if_generation_match=generation)
    except BenchCloudError as exc:
        detail = f"{detail}; recording failed also failed: {exc}"
    log_launch_event("bench_vm_launch_failed", run_id=plan.run_id, zone=zone, detail=detail)
    raise LaunchFailed(f"launch of run {plan.run_id} failed: {detail}")
