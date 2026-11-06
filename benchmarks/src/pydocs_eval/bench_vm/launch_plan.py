"""The launch plan: everything a launch writes and creates, computed before any of it happens.

A first launch plans attempt 1 and a fresh record; a relaunch plans attempt
n+1 over the run's existing record (``PriorRun``) and its frozen commits.

The real launch and the dry run build the same ``LaunchPlan``. The dry run
fills what only the cloud knows (deployment names, the image digest, how a
GPU attaches) with marked placeholders and prints the plan; the real launch
fills them from ``terraform output`` and the cloud, then executes it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from pydocs_eval.bench_vm.deployment import DeploymentNames
from pydocs_eval.bench_vm.run_commits import (
    FrozenCommits,
    GitCheckout,
    HostScripts,
    check_recipe_at_commit,
    check_run_inputs_at_commit,
    freeze_commits,
    read_host_scripts,
    require_recorded_commits,
)
from pydocs_eval.bench_vm.run_id import bench_vm_instance_name
from pydocs_eval.bench_vm.run_record import (
    RUN_RECORD_OBJECT_NAME,
    RUN_RECORD_SCHEMA_VERSION,
    SPEC_COPY_OBJECT_NAME,
    RunRecord,
    RunStatus,
    RunTransition,
)
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile
from pydocs_eval.bench_vm.vm_request import (
    METADATA_ATTEMPT,
    METADATA_CACHE_BUCKET,
    METADATA_IMAGE,
    METADATA_IMAGE_DIGEST,
    METADATA_RUN_ID,
    METADATA_RUNS_BUCKET,
    METADATA_SHUTDOWN_SCRIPT,
    METADATA_STARTUP_SCRIPT,
    GpuAttachment,
    ImageFlavour,
    ProvisioningModel,
    TerminationAction,
    VmCreateRequest,
    image_digest_name,
    image_tag_name,
    vm_labels,
)

FIRST_ATTEMPT = 1


@dataclass(frozen=True, slots=True)
class LaptopChecks:
    """What the laptop established on its own: the frozen commits and the run commit's scripts."""

    commits: FrozenCommits
    flavour: ImageFlavour
    scripts: HostScripts


def run_laptop_checks(git: GitCheckout, spec: RunSpec) -> LaptopChecks:
    """Resolve and freeze the refs; check the inputs, recipe and host scripts at the run commit."""
    return _checks_at_commits(git, spec, freeze_commits(git, spec))


def run_relaunch_checks(git: GitCheckout, spec: RunSpec, record: RunRecord) -> LaptopChecks:
    """The first launch's checks, at the commits the record froze instead of the refs' tips."""
    commits = FrozenCommits(
        run=record.commit, baseline=record.baseline_commit, candidate=record.candidate_commit
    )
    require_recorded_commits(git, commits)
    return _checks_at_commits(git, spec, commits)


def _checks_at_commits(git: GitCheckout, spec: RunSpec, commits: FrozenCommits) -> LaptopChecks:
    check_run_inputs_at_commit(git, spec, commits.run)
    flavour = image_flavour(spec)
    check_recipe_at_commit(git, flavour, commits.run)
    return LaptopChecks(
        commits=commits, flavour=flavour, scripts=read_host_scripts(git, commits.run)
    )


def image_flavour(spec: RunSpec) -> ImageFlavour:
    return ImageFlavour.CPU if spec.gpu is None else ImageFlavour.CUDA


def zone_order(spec: RunSpec, names: DeploymentNames) -> tuple[str, ...]:
    """The spec's ``zone`` first when set, then the region's zones from Terraform."""
    preferred = () if spec.zone is None else (spec.zone,)
    return tuple(dict.fromkeys((*preferred, *names.zones)))


def run_prefix_uri(names: DeploymentNames, run_id: str) -> str:
    return f"gs://{names.runs_bucket}/{run_id}/"


def run_record_uri(names: DeploymentNames, run_id: str) -> str:
    return run_prefix_uri(names, run_id) + RUN_RECORD_OBJECT_NAME


@dataclass(frozen=True, slots=True)
class PriorRun:
    """The run record a relaunch builds on, and the generation it was read (or written) at."""

    record: RunRecord
    generation: int


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """A launch, fully decided: the record (and, first time, the spec copy) to write, the VM.

    ``record_generation`` is the precondition of the record write: 0 on a first
    launch (the record must not exist), the prior record's on a relaunch.
    """

    spec_file: RunSpecFile
    spec_bytes: bytes
    names: DeploymentNames
    checks: LaptopChecks
    zones: tuple[str, ...]
    record: RunRecord
    vm_request: VmCreateRequest
    record_generation: int = 0

    @property
    def is_relaunch(self) -> bool:
        return self.record.attempt > FIRST_ATTEMPT

    @property
    def run_id(self) -> str:
        return self.spec_file.run_id

    @property
    def results_uri(self) -> str:
        return run_prefix_uri(self.names, self.run_id)

    @property
    def record_uri(self) -> str:
        return run_record_uri(self.names, self.run_id)

    @property
    def spec_copy_uri(self) -> str:
        return self.results_uri + SPEC_COPY_OBJECT_NAME

    def request_in(self, zone: str) -> VmCreateRequest:
        return replace(self.vm_request, zone=zone)


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """What the cloud side contributed to the plan (or placeholders for it, in a dry run).

    ``zones`` is the launch order: the spec's zone first, then Terraform's, less
    any zone the Compute API says lacks the machine type.
    """

    names: DeploymentNames
    zones: tuple[str, ...]
    image_digest: str
    gpu: GpuAttachment | None


def build_launch_plan(
    spec_file: RunSpecFile,
    spec_bytes: bytes,
    checks: LaptopChecks,
    inputs: PlanInputs,
    clock: Callable[[], datetime],
    prior: PriorRun | None = None,
) -> LaunchPlan:
    attempt = FIRST_ATTEMPT if prior is None else prior.record.attempt + 1
    transition = _launching_transition(spec_file, inputs.zones, attempt, clock(), prior)
    if prior is None:
        record = _first_record(spec_file, spec_bytes, checks, inputs, transition)
    else:
        record = _relaunch_record(prior.record, checks, inputs, transition)
    return LaunchPlan(
        spec_file=spec_file,
        spec_bytes=spec_bytes,
        names=inputs.names,
        checks=checks,
        zones=inputs.zones,
        record=record,
        vm_request=_vm_request(spec_file, checks, inputs, inputs.zones[0], attempt),
        record_generation=0 if prior is None else prior.generation,
    )


def spec_digest(spec_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(spec_bytes).hexdigest()


def _first_record(
    spec_file: RunSpecFile,
    spec_bytes: bytes,
    checks: LaptopChecks,
    inputs: PlanInputs,
    launching: RunTransition,
) -> RunRecord:
    spec, commits = spec_file.spec, checks.commits
    return RunRecord(
        schema_version=RUN_RECORD_SCHEMA_VERSION,
        run_id=spec_file.run_id,
        spec_digest=spec_digest(spec_bytes),
        kind=spec.kind,
        git_ref=spec.git_ref,
        commit=commits.run,
        baseline_ref=spec.baseline_ref,
        baseline_commit=commits.baseline,
        candidate_ref=spec.candidate_ref,
        candidate_commit=commits.candidate,
        image=_image(inputs, checks),
        image_digest=inputs.image_digest,
        status=RunStatus.LAUNCHING,
        attempt=FIRST_ATTEMPT,
        history=(launching,),
    )


def _relaunch_record(
    prior: RunRecord, checks: LaptopChecks, inputs: PlanInputs, launching: RunTransition
) -> RunRecord:
    # The image is looked up again: a cleanup policy may have removed it, and a
    # rebuild of the same commit gets a new digest.
    rebased = prior.model_copy(
        update={"image": _image(inputs, checks), "image_digest": inputs.image_digest}
    )
    return rebased.with_transition(launching)


def _image(inputs: PlanInputs, checks: LaptopChecks) -> str:
    return image_tag_name(inputs.names.image_repository, checks.flavour, checks.commits.run)


def _launching_transition(
    spec_file: RunSpecFile,
    zones: tuple[str, ...],
    attempt: int,
    at: datetime,
    prior: PriorRun | None,
) -> RunTransition:
    # The zone stays unset: it is known only once a zone has capacity, and the
    # supervisor's `running` transition records where the VM actually runs.
    relaunch = "" if prior is None else f"relaunch after {prior.record.status}; "
    return RunTransition(
        status=RunStatus.LAUNCHING,
        at=at,
        attempt=attempt,
        instance=bench_vm_instance_name(spec_file.run_id, attempt),
        zone=None,
        machine=spec_file.spec.resolved_machine_type,
        detail=f"{relaunch}zones to try: {', '.join(zones)}",
    )


def _vm_request(
    spec_file: RunSpecFile, checks: LaptopChecks, inputs: PlanInputs, zone: str, attempt: int
) -> VmCreateRequest:
    spec, names = spec_file.spec, inputs.names
    return VmCreateRequest(
        project=names.project_id,
        zone=zone,
        name=bench_vm_instance_name(spec_file.run_id, attempt),
        machine_type=spec.resolved_machine_type,
        provisioning_model=ProvisioningModel.SPOT if spec.spot else ProvisioningModel.STANDARD,
        max_run_duration_s=int(spec.max_runtime.total_seconds()),
        termination_action=TerminationAction.DELETE,
        external_ip=False,
        service_account=names.bench_service_account,
        subnetwork=names.subnetwork,
        boot_image=names.boot_image(checks.flavour),
        boot_disk_gb=spec.disk_gb,
        boot_disk_auto_delete=True,
        gpu=inputs.gpu,
        labels=vm_labels(spec_file.run_id, attempt),
        metadata=_vm_metadata(spec_file.run_id, attempt, checks, inputs),
    )


def _vm_metadata(
    run_id: str, attempt: int, checks: LaptopChecks, inputs: PlanInputs
) -> dict[str, str]:
    names = inputs.names
    return {
        METADATA_RUN_ID: run_id,
        METADATA_ATTEMPT: str(attempt),
        METADATA_RUNS_BUCKET: names.runs_bucket,
        METADATA_CACHE_BUCKET: names.cache_bucket,
        METADATA_IMAGE: image_digest_name(
            names.image_repository, checks.flavour, inputs.image_digest
        ),
        METADATA_IMAGE_DIGEST: inputs.image_digest,
        METADATA_STARTUP_SCRIPT: checks.scripts.startup,
        METADATA_SHUTDOWN_SCRIPT: checks.scripts.shutdown,
    }
