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
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydocs_eval.bench_vm.deployment import TERRAFORM_DIR, TerraformDeploymentNames
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.run_control import (
    LOCAL_RUNS_RESULTS_DIR,
    NamedRun,
    RunControlPorts,
    RunControlRefused,
    StoppedRun,
    list_live_bench_vms,
    name_run,
    pull_run,
    read_run_status,
    stop_run,
)
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.run_status_view import render_live_bench_vms, render_run_status


class RunControlVerb(StrEnum):
    """The run-control verbs, as the command line spells them."""

    STATUS = "status"
    STOP = "stop"
    PULL = "pull"


RUN_CONTROL_VERBS = frozenset(RunControlVerb)
_EXIT_OK = 0
_EXIT_REFUSED = 1
# What a verb may fail with that is the owner's to read, not a traceback:
# refusals, cloud and ``terraform output`` errors, a missing or hung binary.
_VERB_ERRORS = (ValueError, RuntimeError, OSError, subprocess.SubprocessError)


def add_run_control_verbs(verbs: argparse._SubParsersAction) -> None:
    status = verbs.add_parser(
        RunControlVerb.STATUS,
        help="print a run's record and log tail; with no spec, every live bench VM",
    )
    status.add_argument("spec", type=Path, nargs="?", help="the run spec (omit: list live VMs)")
    stop = verbs.add_parser(
        RunControlVerb.STOP, help="delete the run's live VM; its supervisor records the run stopped"
    )
    stop.add_argument("spec", type=Path, help="the run spec, e.g. infra/bench-vm/runs/x.yaml")
    pull = verbs.add_parser(
        RunControlVerb.PULL,
        help="download the run's results, spec copy and record into a results directory",
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
    run = name_run(load_run_spec(args.spec), ports)
    return _VERB_HANDLERS[RunControlVerb(args.verb)](args, ports, run)


def _status(args: argparse.Namespace, ports: RunControlPorts, run: NamedRun) -> str:
    return render_run_status(read_run_status(ports.cloud, run))


def _stop(args: argparse.Namespace, ports: RunControlPorts, run: NamedRun) -> str:
    stopped = stop_run(ports.cloud, run)
    deleted = [f"deleted {vm.name} in {vm.zone}" for vm in stopped.deleted]
    return "\n".join([*deleted, f"run {run.run_id}: {_after_the_stop(run, stopped)}"])


def _after_the_stop(run: NamedRun, stopped: StoppedRun) -> str:
    """Who records the stop, if anyone: it depends on where the run was."""
    relaunch = f"relaunch it later with {run.make_launch_command}"
    record = stopped.record
    if record is None:
        return f"its run record is unreadable, so nothing here can say how it ends; {relaunch}"
    if record.status is RunStatus.RUNNING:
        return f"its supervisor records stopped as the VM shuts down; {relaunch}"
    if record.status is RunStatus.LAUNCHING:
        return (
            "it was still launching, before its supervisor started, so nothing records "
            f"stopped; the next launch records it lost and relaunches it: {run.make_launch_command}"
        )
    return f"the run had already ended {record.status}; its record stays {record.status}"


def _pull(args: argparse.Namespace, ports: RunControlPorts, run: NamedRun) -> str:
    destination = pull_run(ports.cloud, run, args.results_dir)
    return f"run {run.run_id}: pulled {run.prefix_uri} into {destination}"


_VERB_HANDLERS: dict[
    RunControlVerb, Callable[[argparse.Namespace, RunControlPorts, NamedRun], str]
] = {
    RunControlVerb.STATUS: _status,
    RunControlVerb.STOP: _stop,
    RunControlVerb.PULL: _pull,
}
