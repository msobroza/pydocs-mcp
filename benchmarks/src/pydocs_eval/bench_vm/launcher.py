"""The launcher: ``make bench-vm RUN=runs/<name>.yaml`` launches a run, or relaunches an ended one.

In order (spec "Launcher"): load the spec; read the deployment's names from
``terraform output``; compute the run id and read the run record; on a first
launch resolve and freeze the commits, on a relaunch apply the relaunch rules
(``relaunch``) and reuse the recorded commits; check the configs and ``corpus_dir`` at the commit; check
the machine type through the Compute API; ensure the image, building it only
when its tag is missing (``image_on_demand``); write the run record and the
verbatim spec copy; create the VM, moving to the next zone on a stockout only.
Everything that can fail on the laptop fails before anything is written or
created, a failed image build too, and once the record exists, any failure is
recorded as ``failed`` rather than leaving a ``launching`` run with no VM.
The one write before that point is a relaunch's ``lost``, the truth about a
record whose VM vanished, made only once every check has passed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloud,
    BenchCloudError,
    StaleGenerationError,
    ZoneStockoutError,
)
from pydocs_eval.bench_vm.deployment import DeploymentNames, DeploymentNamesSource
from pydocs_eval.bench_vm.image_on_demand import ensure_run_image
from pydocs_eval.bench_vm.launch_events import log_launch_event
from pydocs_eval.bench_vm.launch_plan import (
    LaunchPlan,
    PlanInputs,
    PriorRun,
    build_launch_plan,
    run_laptop_checks,
    run_prefix_uri,
    run_record_uri,
    run_relaunch_checks,
    spec_digest,
    zone_order,
)
from pydocs_eval.bench_vm.machine_check import check_machine_type, refuse_unoffered_machine_type
from pydocs_eval.bench_vm.relaunch import (
    read_prior_run,
    record_lost_if_vm_vanished,
    refuse_live_vm,
    refuse_unrelaunchable,
)
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.run_record import RunStatus, RunTransition, run_record_bytes
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile
from pydocs_eval.bench_vm.vm_request import GpuAttachment


@dataclass(frozen=True, slots=True)
class LaunchPorts:
    """The launcher's external I/O: the cloud, ``terraform output``, the clock."""

    cloud: BenchCloud
    deployment_names: DeploymentNamesSource
    clock: Callable[[], datetime]


class LaunchFailed(RuntimeError):
    """The launch failed after the run record was written; the record says ``failed``."""


def launch_or_relaunch(
    spec_file: RunSpecFile, spec_bytes: bytes, git: GitCheckout, ports: LaunchPorts
) -> LaunchPlan:
    """Launch the spec's run: attempt 1 without a record, attempt n+1 over an ended one."""
    names = ports.deployment_names.read_deployment_names()
    uri = run_record_uri(names, spec_file.run_id)
    prior = read_prior_run(ports.cloud, uri)
    if prior is None:
        checks = run_laptop_checks(git, spec_file.spec)
    else:
        refuse_unrelaunchable(prior, run_prefix_uri(names, spec_file.run_id))
        checks = run_relaunch_checks(git, spec_file.spec, prior.record)
    zones, gpu = _zones_offering_the_machine(ports.cloud, names, spec_file)
    digest = ensure_run_image(ports.cloud, git, names, checks.flavour, checks.commits.run)
    inputs = PlanInputs(names=names, zones=zones, image_digest=digest, gpu=gpu)
    if prior is not None:
        prior = _settle_prior_run(ports, names, spec_file, prior, uri)
    plan = build_launch_plan(spec_file, spec_bytes, checks, inputs, ports.clock, prior)
    _execute(ports, plan)
    return plan


def _settle_prior_run(
    ports: LaunchPorts, names: DeploymentNames, spec_file: RunSpecFile, prior: PriorRun, uri: str
) -> PriorRun:
    """Refuse a live VM, then record a vanished one ``lost``.

    It runs after the image is ensured, which can take a build's length, so the
    no-VM answer the writes act on is as fresh as it can be.
    """
    searched = zone_order(spec_file.spec, names)
    refuse_live_vm(ports.cloud, names.project_id, searched, spec_file.run_id)
    return record_lost_if_vm_vanished(
        ports.cloud, prior, uri=uri, zones=searched, clock=ports.clock
    )


def _execute(ports: LaunchPorts, plan: LaunchPlan) -> None:
    generation = _write_launching_record(ports.cloud, plan)
    try:
        _copy_spec(ports.cloud, plan)
        _create_vm(ports, plan, generation)
    except LaunchFailed:
        raise
    except Exception as exc:  # whatever broke, never leave `launching` without a VM
        _record_failed(ports, plan, generation, zone=None, detail=f"{type(exc).__name__}: {exc}")


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


def _write_launching_record(cloud: BenchCloud, plan: LaunchPlan) -> int:
    """Claim the attempt: a first launch writes at generation 0 (the record must not exist),
    a relaunch at the generation it read, so a concurrent writer makes it fail."""
    try:
        generation = cloud.write_object(
            plan.record_uri,
            run_record_bytes(plan.record),
            if_generation_match=plan.record_generation,
        )
    except BenchCloudError as exc:
        raise LaunchFailed(f"writing the run record failed: {exc}") from None
    log_launch_event(
        "bench_vm_record_written",
        run_id=plan.run_id,
        status=RunStatus.LAUNCHING,
        attempt=plan.record.attempt,
    )
    return generation


def _copy_spec(cloud: BenchCloud, plan: LaunchPlan) -> None:
    """Upload the verbatim spec once, next to the record, and never overwrite it.

    The first launch uploads it. A relaunch uploads it only when an earlier
    attempt failed before it did, and only bytes matching the record's digest;
    a copy that is already there wins the generation-0 write.
    """
    if not plan.is_relaunch:
        cloud.write_object(plan.spec_copy_uri, plan.spec_bytes, if_generation_match=0)
        return
    if spec_digest(plan.spec_bytes) != plan.record.spec_digest:
        return
    try:
        cloud.write_object(plan.spec_copy_uri, plan.spec_bytes, if_generation_match=0)
    except StaleGenerationError:
        return


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
