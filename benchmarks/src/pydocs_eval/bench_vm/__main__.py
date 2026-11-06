"""``python -m pydocs_eval.bench_vm``: the bench VM tooling that runs on the laptop.

- ``check SPEC...`` validates run specs and prints ``<spec>: <run id>``, one
  ``<spec>: <field>: got …, expected …`` line per problem on stderr; exit 1
  when any spec is refused. No credentials, no network, no git —
  ``make bench-vm-check`` runs it over every committed spec in CI.
- ``launch SPEC`` (``make bench-vm RUN=runs/<name>.yaml``) launches the first
  attempt of a run and prints its run id, results URI and status command,
  without waiting for it. ``--dry-run`` (``DRY_RUN=1``) runs every check the
  laptop can make on its own and prints the plan; it never runs ``gcloud`` or
  ``terraform`` and writes nothing.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydocs_eval.bench_vm.deployment import TERRAFORM_DIR, TerraformDeploymentNames
from pydocs_eval.bench_vm.dry_run import plan_dry_run, render_dry_run
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.launch_plan import LaunchPlan
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.launcher import LaunchPorts, launch_first_run
from pydocs_eval.bench_vm.run_commits import GitCheckout
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile, load_run_spec

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
        "launch", help="launch the first attempt of a run on a bench VM, without waiting for it"
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
    return parser


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
        plan = launch_first_run(spec_file, spec_bytes, git, ports)
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


def main(argv: Sequence[str] | None = None, *, launch_ports: LaunchPorts | None = None) -> int:
    args = build_bench_vm_parser().parse_args(argv)
    if args.verb == "check":
        return check_run_specs(args.specs)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return launch_run(args, launch_ports)


if __name__ == "__main__":
    sys.exit(main())
