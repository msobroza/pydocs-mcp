"""The laptop-only verbs that check a run against a local reference: ``local-command``.

They read the spec and the local disk only: no ``gcloud``, no ``terraform``,
no credentials.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from enum import StrEnum
from pathlib import Path

from pydocs_eval.bench_vm.local_reference import (
    LOCAL_REFERENCE_RESULTS_DIR,
    LocalReferenceRefused,
    local_reference_argv,
)
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec


class LocalReferenceVerb(StrEnum):
    """The local-reference verbs, as the command line spells them."""

    LOCAL_COMMAND = "local-command"


LOCAL_REFERENCE_VERBS = frozenset(LocalReferenceVerb)
_EXIT_OK = 0
_EXIT_REFUSED = 1


def add_local_reference_verbs(verbs: argparse._SubParsersAction) -> None:
    local = verbs.add_parser(
        LocalReferenceVerb.LOCAL_COMMAND,
        help="print the runner command for an uninterrupted local run of a retrieval spec",
    )
    local.add_argument("spec", type=Path, help="the run spec, e.g. infra/bench-vm/runs/x.yaml")
    local.add_argument(
        "--results-dir",
        type=Path,
        default=LOCAL_REFERENCE_RESULTS_DIR,
        help=f"where <run id>/ lands (default {LOCAL_REFERENCE_RESULTS_DIR}, git-ignored)",
    )


def local_reference_verb(args: argparse.Namespace) -> int:
    """Run ``local-command`` and return the exit code."""
    try:
        argv = local_reference_argv(load_run_spec(args.spec), args.results_dir)
    except RunSpecError as exc:
        for error in exc.errors:
            print(f"{args.spec}: {error.render()}", file=sys.stderr)
        return _EXIT_REFUSED
    except LocalReferenceRefused as exc:
        print(f"{args.spec}: {args.verb} refused: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    print(shlex.join(argv))
    return _EXIT_OK
