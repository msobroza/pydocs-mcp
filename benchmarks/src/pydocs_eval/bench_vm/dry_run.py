"""``launch --dry-run``: run every check the laptop can make alone, then print the launch plan.

A dry run never invokes ``gcloud``, ``terraform`` or the network, and writes
nothing. It loads the spec, derives the run id, resolves the refs and checks
they are on a remote-tracking branch, and checks the recipe, the configs,
``corpus_dir`` and the host scripts at the commit — the same code a real launch
runs. What only the cloud knows (the deployment's names, the image digest, the
machine-type facts, whether the image tag exists, an existing record, a live
VM) is shown as a marked placeholder or listed as not checked. So the plan shows
the build a missing tag would submit without saying whether it will run, and
shows a first launch while saying a relaunch was not ruled out.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydocs_eval.bench_vm.commit_layout import (
    CLOUDBUILD_CONFIG,
    HOST_SHUTDOWN_SCRIPT,
    HOST_STARTUP_SCRIPT,
)
from pydocs_eval.bench_vm.deployment import placeholder_deployment_names
from pydocs_eval.bench_vm.image_build_request import (
    COMMIT_ARCHIVE_NAME,
    image_build_substitutions,
)
from pydocs_eval.bench_vm.launch_plan import (
    LaunchPlan,
    PlanInputs,
    build_launch_plan,
    run_laptop_checks,
    spec_digest,
    zone_order,
)
from pydocs_eval.bench_vm.machine_check import MIN_GUEST_CPUS, MIN_MEMORY_MB
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.run_record import run_record_bytes
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile
from pydocs_eval.bench_vm.vm_request import (
    METADATA_SHUTDOWN_SCRIPT,
    METADATA_STARTUP_SCRIPT,
    GpuAttachment,
    VmCreateRequest,
)

_DIGEST_PLACEHOLDER = "<image digest: Artifact Registry, not queried in a dry run>"
_PASSED_CHECKS = (
    "run spec",
    "run id",
    "ref resolution",
    "remote-tracking reachability",
    "recipe at the commit",
    "configs and corpus_dir at the commit",
    "host scripts at the commit",
)
_NOT_CHECKED = (
    f"machine-type facts (x86_64, at least {MIN_GUEST_CPUS} vCPU and {MIN_MEMORY_MB} MB)",
    "image tag in Artifact Registry (whether a build runs)",
    "existing run record (whether this is a relaunch)",
    "live VM",
)
_SCRIPT_PATHS = {
    METADATA_STARTUP_SCRIPT: HOST_STARTUP_SCRIPT,
    METADATA_SHUTDOWN_SCRIPT: HOST_SHUTDOWN_SCRIPT,
}


def plan_dry_run(
    spec_file: RunSpecFile,
    spec_bytes: bytes,
    git: GitCheckout,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LaunchPlan:
    """The plan a first launch would execute, from laptop-only checks and placeholders."""
    checks = run_laptop_checks(git, spec_file.spec)
    gpu = spec_file.spec.gpu
    names = placeholder_deployment_names()
    inputs = PlanInputs(
        names=names,
        zones=zone_order(spec_file.spec, names),
        image_digest=_DIGEST_PLACEHOLDER,
        gpu=None if gpu is None else GpuAttachment(str(gpu.type), gpu.count, False),
    )
    return build_launch_plan(spec_file, spec_bytes, checks, inputs, clock)


def render_dry_run(plan: LaunchPlan) -> str:
    sections = (
        _header(plan),
        _commits(plan),
        _image_build(plan),
        _first_launch_or_relaunch(),
        _writes(plan),
        _vm_request(plan.vm_request, plan),
        _checks(),
    )
    return "\n\n".join("\n".join(lines) for lines in sections)


def _header(plan: LaunchPlan) -> list[str]:
    return [
        "DRY RUN: nothing was created or written; gcloud and terraform were not run.",
        "Values in <angle brackets> are placeholders for what only the cloud knows.",
        "",
        f"spec: {plan.spec_file.path}",
        f"run id: {plan.run_id}",
        f"results: {plan.results_uri}",
    ]


def _commits(plan: LaunchPlan) -> list[str]:
    record = plan.record
    lines = [
        "commits, frozen in the run record:",
        f"  git_ref: {record.git_ref} -> {record.commit}",
    ]
    if record.baseline_ref is not None:
        lines.append(f"  baseline_ref: {record.baseline_ref} -> {record.baseline_commit}")
    if record.candidate_ref is not None:
        lines.append(f"  candidate_ref: {record.candidate_ref} -> {record.candidate_commit}")
    lines.append(f"image ({plan.checks.flavour} flavour): {record.image}")
    return lines


def _image_build(plan: LaunchPlan) -> list[str]:
    names, commit, flavour = plan.names, plan.record.commit, plan.checks.flavour
    substitutions = image_build_substitutions(names.image_repository, flavour, commit)
    return [
        "image build:",
        "  only if the tag is missing: not checked (dry run)",
        f"  Cloud Build in {names.region}, project {names.project_id}",
        f"  source: {COMMIT_ARCHIVE_NAME}, a git archive of {commit}",
        f"  config: {CLOUDBUILD_CONFIG} at {commit}",
        f"  substitutions: {', '.join(f'{k}={v}' for k, v in substitutions.items())}",
        "  the tag's digest, built or found, goes into the record and the VM metadata",
    ]


def _first_launch_or_relaunch() -> list[str]:
    return [
        "first launch or relaunch: not checked (dry run)",
        "  this plan is a first launch, attempt 1, on the commits resolved above",
        "  over an existing run record, a relaunch would instead reuse its recorded commits as "
        "attempt n+1 (the checks above ran at the refs' current commits, not those), and a "
        "succeeded run or a live VM would be refused",
    ]


def _writes(plan: LaunchPlan) -> list[str]:
    record_json = run_record_bytes(plan.record).decode("utf-8").rstrip("\n")
    return [
        "run record, written with generation precondition 0 (it must not exist yet):",
        f"  {plan.record_uri}",
        *(f"  {line}" for line in record_json.splitlines()),
        "spec copy, written once with generation precondition 0:",
        f"  {plan.spec_copy_uri} ({len(plan.spec_bytes)} bytes, {spec_digest(plan.spec_bytes)})",
    ]


def _vm_request(request: VmCreateRequest, plan: LaunchPlan) -> list[str]:
    labels = ", ".join(f"{key}={value}" for key, value in request.labels.items())
    return [
        "VM create request:",
        f"  name: {request.name}",
        f"  project: {request.project}",
        f"  zone order: {', '.join(plan.zones)} (the next zone only on a stockout)",
        f"  machine type: {request.machine_type}",
        f"  provisioning model: {request.provisioning_model}",
        f"  max run duration: {request.max_run_duration_s}s, then {request.termination_action}",
        "  external IP: none",
        f"  service account: {request.service_account}",
        f"  subnetwork: {request.subnetwork}",
        f"  boot image: {request.boot_image}",
        f"  boot disk: {request.boot_disk_gb} GB, deleted with the VM",
        f"  gpu: {_gpu_text(request.gpu)}",
        f"  labels: {labels}",
        "  metadata:",
        *(f"    {line}" for line in _metadata_lines(request, plan.record.commit)),
    ]


def _gpu_text(gpu: GpuAttachment | None) -> str:
    if gpu is None:
        return "none"
    return f"{gpu.accelerator_type} x {gpu.count} (how it attaches depends on machine-type facts)"


def _metadata_lines(request: VmCreateRequest, commit: str) -> list[str]:
    lines = []
    for key, value in request.metadata.items():
        path = _SCRIPT_PATHS.get(key)
        shown = value if path is None else f"{path} at {commit} ({len(value.encode())} bytes)"
        lines.append(f"{key}: {shown}")
    return lines


def _checks() -> list[str]:
    return [
        "checks:",
        f"  passed: {', '.join(_PASSED_CHECKS)}",
        f"  not checked (dry run): {', '.join(_NOT_CHECKED)}",
    ]
