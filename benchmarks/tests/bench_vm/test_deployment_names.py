"""Deployment names come only from ``terraform output -json``; a dry run shows placeholders."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.deployment import (
    DeploymentNames,
    DeploymentNamesError,
    TerraformDeploymentNames,
    deployment_names_from_outputs,
    placeholder_deployment_names,
)

from ._fakes import ScriptedCommandRunner, failed, fake_deployment_names, ok
from ._spec_dicts import REPO_ROOT

_OUTPUTS_TF = REPO_ROOT / "infra" / "bench-vm" / "terraform" / "outputs.tf"


def _outputs_json(names: DeploymentNames) -> dict[str, object]:
    outputs: dict[str, object] = {}
    for field in fields(DeploymentNames):
        value = getattr(names, field.name)
        outputs[field.name] = {"value": list(value) if isinstance(value, tuple) else value}
    outputs["build_service_account"] = {"value": "projects/p/serviceAccounts/build@p"}
    return outputs


def test_every_name_the_launcher_reads_is_a_terraform_output() -> None:
    declared = set(re.findall(r'^output "([a-z_]+)"', _OUTPUTS_TF.read_text(), re.MULTILINE))

    assert {field.name for field in fields(DeploymentNames)} <= declared


def test_terraform_output_json_maps_to_deployment_names() -> None:
    expected = fake_deployment_names()
    runner = ScriptedCommandRunner(script=[ok(json.dumps(_outputs_json(expected)))])

    names = TerraformDeploymentNames(
        Path("infra/bench-vm/terraform"), runner
    ).read_deployment_names()

    assert names == expected
    (call,) = runner.calls
    assert call.argv == ("terraform", "-chdir=infra/bench-vm/terraform", "output", "-json")
    assert call.timeout_s > 0


def test_a_missing_output_is_refused_naming_it() -> None:
    outputs = _outputs_json(fake_deployment_names())
    del outputs["runs_bucket"]

    with pytest.raises(DeploymentNamesError, match="runs_bucket"):
        deployment_names_from_outputs(outputs)


def test_a_failing_terraform_output_is_refused_with_its_stderr() -> None:
    runner = ScriptedCommandRunner(script=[failed("Error: No outputs found")])

    with pytest.raises(DeploymentNamesError, match="No outputs found"):
        TerraformDeploymentNames(Path("tf"), runner).read_deployment_names()


def test_placeholders_are_marked_and_never_look_like_real_names() -> None:
    names = placeholder_deployment_names()

    for field in fields(DeploymentNames):
        value = getattr(names, field.name)
        for item in value if isinstance(value, tuple) else (value,):
            assert item.startswith("<") and item.endswith(">")
            assert "not read in a dry run" in item
