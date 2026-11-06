"""The deployment's names, read from ``terraform output`` — their single source.

The launcher never hard-codes a project, bucket, registry, service account,
subnet, zone or boot image: it reads them from the bench VM Terraform root's
outputs (``infra/bench-vm/terraform/outputs.tf``). Tests inject them through a
named fake; a dry run shows them as clearly marked placeholders and never runs
``terraform``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Protocol

from pydocs_eval.bench_vm.bounded_command import CommandRunner, run_bounded, run_subprocess
from pydocs_eval.bench_vm.vm_request import ImageFlavour

TERRAFORM_DIR = Path("infra/bench-vm/terraform")
_TERRAFORM_TIMEOUT_S = 120.0


@dataclass(frozen=True, slots=True)
class DeploymentNames:
    """The Terraform outputs the launcher reads, one field per output of the same name."""

    project_id: str
    region: str
    zones: tuple[str, ...]
    runs_bucket: str
    cache_bucket: str
    image_repository: str
    bench_service_account: str
    subnetwork: str
    cpu_boot_image: str
    gpu_boot_image: str

    def boot_image(self, flavour: ImageFlavour) -> str:
        return self.cpu_boot_image if flavour is ImageFlavour.CPU else self.gpu_boot_image


class DeploymentNamesSource(Protocol):
    def read_deployment_names(self) -> DeploymentNames: ...


class DeploymentNamesError(ValueError):
    """``terraform output`` did not yield every name the launcher needs."""


def deployment_names_from_outputs(outputs: Mapping[str, object]) -> DeploymentNames:
    """Map ``terraform output -json`` (``{name: {"value": …}}``) to ``DeploymentNames``."""
    values: dict[str, object] = {}
    for field in fields(DeploymentNames):
        entry = outputs.get(field.name)
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if field.name == "zones":
            value = _zones_from(value)
        if value is None or value == "" or value == ():
            raise DeploymentNamesError(
                f"terraform output {field.name}: got {entry!r}, expected a non-empty value; "
                f"run `terraform apply` in {TERRAFORM_DIR} first"
            )
        values[field.name] = value
    return DeploymentNames(**values)  # type: ignore[arg-type]


def _zones_from(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(zone, str) for zone in value):
        return None
    return tuple(value)


@dataclass(frozen=True, slots=True)
class TerraformDeploymentNames:
    """Reads the names with ``terraform -chdir=<dir> output -json`` (read-only, no apply)."""

    terraform_dir: Path
    runner: CommandRunner = run_subprocess

    def read_deployment_names(self) -> DeploymentNames:
        argv = ("terraform", f"-chdir={self.terraform_dir}", "output", "-json")
        completed = run_bounded(self.runner, argv, timeout_s=_TERRAFORM_TIMEOUT_S)
        if completed.returncode != 0:
            raise DeploymentNamesError(
                f"terraform output in {self.terraform_dir}: got exit {completed.returncode} "
                f"({completed.stderr_text}), expected the bench VM root's outputs"
            )
        try:
            outputs = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DeploymentNamesError(
                f"terraform output -json: got unreadable JSON ({exc}), expected a JSON object"
            ) from None
        return deployment_names_from_outputs(outputs)


def placeholder(output_name: str) -> str:
    """A value a dry run could not know, marked so it cannot be mistaken for a real name."""
    return f"<{output_name}: terraform output, not read in a dry run>"


def placeholder_deployment_names() -> DeploymentNames:
    """Every name as a marked placeholder: what a dry run shows instead of reading Terraform."""
    values: dict[str, object] = {f.name: placeholder(f.name) for f in fields(DeploymentNames)}
    values["zones"] = (placeholder("zones"),)
    return DeploymentNames(**values)  # type: ignore[arg-type]
