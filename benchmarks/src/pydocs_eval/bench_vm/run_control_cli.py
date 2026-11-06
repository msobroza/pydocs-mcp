"""The ``status``, ``stop`` and ``pull`` verbs of ``python -m pydocs_eval.bench_vm``.

``make bench-vm-status [RUN=runs/<name>.yaml]``, ``make bench-vm-stop RUN=…``
and ``make bench-vm-pull RUN=…`` call them; ``run_control`` holds what they do.
Like ``launch``, they run as the owner's ``gcloud auth login`` session and
read the deployment's names from ``terraform output``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import ListedInstance
from pydocs_eval.bench_vm.deployment import TERRAFORM_DIR, TerraformDeploymentNames
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.run_control import (
    LOCAL_RUNS_RESULTS_DIR,
    NamedRun,
    RunControlPorts,
    RunControlRefused,
    list_live_bench_vms,
    name_run,
    pull_run,
    read_run_status,
    stop_run,
)
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.run_status_view import render_live_bench_vms, render_run_status

RUN_CONTROL_VERBS = frozenset({"status", "stop", "pull"})
_EXIT_OK = 0
_EXIT_REFUSED = 1
# What a verb may fail with that is the owner's to read, not a traceback:
# refusals, cloud and ``terraform output`` errors, a missing or hung binary.
_VERB_ERRORS = (ValueError, RuntimeError, OSError, subprocess.SubprocessError)


def add_run_control_verbs(verbs: argparse._SubParsersAction) -> None:
    status = verbs.add_parser(
        "status", help="print a run's record and log tail; with no spec, every live bench VM"
    )
    status.add_argument("spec", type=Path, nargs="?", help="the run spec (omit: list live VMs)")
    stop = verbs.add_parser(
        "stop", help="delete the run's live VM; its supervisor records the run stopped"
    )
    stop.add_argument("spec", type=Path, help="the run spec, e.g. infra/bench-vm/runs/x.yaml")
    pull = verbs.add_parser(
        "pull", help="download the run's results, spec copy and record into a results directory"
    )
    pull.add_argument("spec", type=Path, help="the run spec, e.g. infra/bench-vm/runs/x.yaml")
    pull.add_argument(
        "--results-dir",
        type=Path,
        default=LOCAL_RUNS_RESULTS_DIR,
        help=f"where <run id>/ lands (default {LOCAL_RUNS_RESULTS_DIR}, git-ignored)",
    )


def default_run_control_ports() -> RunControlPorts:
    """The real ports: ``gcloud`` as the owner's login session, ``terraform output``."""
    return RunControlPorts(
        cloud=GcloudBenchCloud(),
        deployment_names=TerraformDeploymentNames(terraform_dir=TERRAFORM_DIR),
    )


def run_control_verb(args: argparse.Namespace, ports: RunControlPorts | None) -> int:
    """Run ``status``, ``stop`` or ``pull`` and return the exit code."""
    subject = args.spec if args.spec is not None else "bench VMs"
    try:
        print(_run_verb(args, ports or default_run_control_ports()))
    except RunSpecError as exc:
        for error in exc.errors:
            print(f"{args.spec}: {error.render()}", file=sys.stderr)
        return _EXIT_REFUSED
    except _VERB_ERRORS as exc:
        outcome = "refused" if isinstance(exc, RunControlRefused) else "failed"
        print(f"{subject}: {args.verb} {outcome}: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    return _EXIT_OK


def _run_verb(args: argparse.Namespace, ports: RunControlPorts) -> str:
    if args.spec is None:
        return render_live_bench_vms(*list_live_bench_vms(ports))
    spec_file = load_run_spec(args.spec)
    run = name_run(spec_file, ports)
    if args.verb == "status":
        return render_run_status(read_run_status(ports.cloud, run))
    if args.verb == "stop":
        return _render_stopped(run, stop_run(ports.cloud, run))
    destination = pull_run(ports.cloud, run, args.results_dir)
    return f"run {run.run_id}: pulled {run.prefix_uri} into {destination}"


def _render_stopped(run: NamedRun, stopped: tuple[ListedInstance, ...]) -> str:
    return "\n".join(
        [
            *(f"deleted {vm.name} in {vm.zone}" for vm in stopped),
            f"run {run.run_id}: its supervisor records stopped as the VM shuts down; "
            f"relaunch it later with {run.make_launch_command}",
        ]
    )
