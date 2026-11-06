"""``python -m pydocs_eval.bench_vm``: the bench VM tooling, on the laptop and on the VM.

- ``check SPEC...`` validates run specs and prints ``<spec>: <run id>``, one
  ``<spec>: <field>: got …, expected …`` line per problem on stderr; exit 1
  when any spec is refused. No credentials, no network, no git —
  ``make bench-vm-check`` runs it over every committed spec in CI.
- ``launch SPEC`` (``make bench-vm RUN=runs/<name>.yaml``) launches the first
  attempt of a run, or relaunches an ended one as its next attempt, and prints
  its run id, results URI and status command, without waiting for it. ``--dry-run`` (``DRY_RUN=1``) runs every check the
  laptop can make on its own and prints the plan; it never runs ``gcloud`` or
  ``terraform`` and writes nothing.
- ``supervise`` runs on the bench VM, as its container's entrypoint (the host
  startup script starts it): it reads the run from the instance metadata and
  GCS, runs the kind's CLI, syncs the state and ends the run. Exit 0 when the
  run succeeded, 1 when it ended any other way, 2 when it refused to start.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.container_paths import CACHE_DIR, REPO_DIR, STATE_DIR, RunDirectories
from pydocs_eval.bench_vm.deployment import TERRAFORM_DIR, TerraformDeploymentNames
from pydocs_eval.bench_vm.dry_run import plan_dry_run, render_dry_run
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.launch_plan import LaunchPlan
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.launcher import LaunchPorts, launch_or_relaunch
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile, load_run_spec
from pydocs_eval.bench_vm.supervisor import (
    EXIT_REFUSED,
    SupervisorPorts,
    log_supervisor_event,
    supervise_run,
)
from pydocs_eval.bench_vm.supervisor_refusal import SupervisorRefused

_EXIT_OK = 0
_EXIT_REFUSED = 1
# What a launch may fail with that is the owner's to read, not a traceback:
# refusals and cloud errors, plus a missing or hung git/gcloud/terraform binary
# and an unreadable spec file.
_LAUNCH_ERRORS = (ValueError, RuntimeError, OSError, subprocess.SubprocessError)


def build_bench_vm_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pydocs_eval.bench_vm",
        description="Bench VM tooling that runs on the laptop.",
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    check = verbs.add_parser(
        "check", help="validate run specs and print each run id; no credentials, no network"
    )
    check.add_argument("specs", nargs="+", type=Path, help="run-spec YAML files")
    launch = verbs.add_parser(
        "launch", help="launch (or relaunch) a run on a bench VM, without waiting for it"
    )
    launch.add_argument("spec", type=Path, help="the run spec, e.g. infra/bench-vm/runs/x.yaml")
    launch.add_argument(
        "--repo", type=Path, default=Path(), help="the git checkout to resolve refs in (default .)"
    )
    launch.add_argument(
        "--dry-run",
        action="store_true",
        help="run the laptop-only checks and print the plan; no gcloud, terraform or writes",
    )
    _add_supervise_verb(verbs)
    return parser


def _add_supervise_verb(verbs: argparse._SubParsersAction) -> None:
    supervise = verbs.add_parser(
        "supervise",
        help="run this bench VM's run inside its container: the kind's CLI, syncs, the ending",
    )
    for flag, default, what in (
        ("--state-dir", STATE_DIR, "the run's state, mirrored to GCS"),
        ("--cache-dir", CACHE_DIR, "the run's caches"),
        ("--repo-dir", REPO_DIR, "the repository at the run's commit; the child's cwd"),
    ):
        supervise.add_argument(
            flag, type=Path, default=default, help=f"{what} (default {default}, a fixed path)"
        )


def check_run_specs(paths: Sequence[Path]) -> int:
    """Validate every spec in ``paths``, report each, and return the exit code."""
    refused = False
    for path in paths:
        try:
            loaded = load_run_spec(path)
        except RunSpecError as exc:
            refused = True
            _print_spec_errors(path, exc)
            continue
        print(f"{path}: {loaded.run_id}")
    return _EXIT_REFUSED if refused else _EXIT_OK


def _print_spec_errors(path: Path, exc: RunSpecError) -> None:
    for error in exc.errors:
        print(f"{path}: {error.render()}", file=sys.stderr)


def default_launch_ports(repo: Path) -> LaunchPorts:
    """The real ports: ``gcloud`` as the owner's login session, ``terraform output``, UTC now."""
    return LaunchPorts(
        cloud=GcloudBenchCloud(),
        deployment_names=TerraformDeploymentNames(terraform_dir=repo / TERRAFORM_DIR),
        clock=lambda: datetime.now(UTC),
    )


def launch_run(args: argparse.Namespace, launch_ports: LaunchPorts | None) -> int:
    try:
        spec_file, spec_bytes = _load_launch_spec(args.spec)
        git = GitCheckout(root=args.repo)
        if args.dry_run:
            print(render_dry_run(plan_dry_run(spec_file, spec_bytes, git)))
            return _EXIT_OK
        ports = launch_ports or default_launch_ports(args.repo)
        plan = launch_or_relaunch(spec_file, spec_bytes, git, ports)
    except RunSpecError as exc:
        _print_spec_errors(args.spec, exc)
        return _EXIT_REFUSED
    except _LAUNCH_ERRORS as exc:
        outcome = "launch refused" if isinstance(exc, LaunchRefused) else "launch failed"
        print(f"{args.spec}: {outcome}: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    _print_launched(plan)
    return _EXIT_OK


def _load_launch_spec(path: Path) -> tuple[RunSpecFile, bytes]:
    """The validated spec and the exact bytes it was validated from.

    The bytes are read on both sides of validation: the copy uploaded next to
    the record, and its digest, must be the file the run id was derived from.
    """
    before = path.read_bytes()
    spec_file = load_run_spec(path)
    if path.read_bytes() != before:
        raise LaunchRefused(
            f"{path}: got a spec file that changed while it was being read, "
            "expected a file that stays put for the launch; run it again"
        )
    return spec_file, before


def _print_launched(plan: LaunchPlan) -> None:
    print(f"run id: {plan.run_id}")
    print(f"results: {plan.results_uri}")
    print(f"status: make bench-vm-status RUN=runs/{plan.spec_file.path.name}")


def default_supervisor_ports() -> SupervisorPorts:
    """The real ports on the VM: ``gcloud`` and ``curl`` as the VM's service account, UTC now."""
    return SupervisorPorts(cloud=GcloudBenchCloud(), clock=lambda: datetime.now(UTC))


def supervise(args: argparse.Namespace, supervisor_ports: SupervisorPorts | None) -> int:
    dirs = RunDirectories(
        state_dir=args.state_dir, cache_dir=args.cache_dir, repo_dir=args.repo_dir
    )
    try:
        return supervise_run(dirs, supervisor_ports or default_supervisor_ports())
    except SupervisorRefused as exc:
        log_supervisor_event("bench_vm_supervisor_refused", detail=str(exc))
        return EXIT_REFUSED
    except BenchCloudError as exc:  # the run could not even be read; nothing ran
        log_supervisor_event("bench_vm_supervisor_failed", detail=str(exc))
        return EXIT_REFUSED


def main(
    argv: Sequence[str] | None = None,
    *,
    launch_ports: LaunchPorts | None = None,
    supervisor_ports: SupervisorPorts | None = None,
) -> int:
    args = build_bench_vm_parser().parse_args(argv)
    if args.verb == "check":
        return check_run_specs(args.specs)
    if args.verb == "supervise":
        return supervise(args, supervisor_ports)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return launch_run(args, launch_ports)


if __name__ == "__main__":
    sys.exit(main())
