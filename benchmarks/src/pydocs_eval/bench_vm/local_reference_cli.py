"""The laptop-only verbs that check a run against a local reference: ``local-command`` and ``compare``.

``local-command`` prints the runner command for an uninterrupted local run of
a retrieval spec (``local_reference``); ``compare`` (``make bench-vm-compare``)
reads the pulled run's leg files and the local run's, and exits 0 only when
every leg's deterministic aggregates are bit-identical (``leg_comparison``).
Both read the spec and the local disk only: no ``gcloud``, no ``terraform``,
no credentials.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydocs_eval.bench_vm.leg_comparison import LegFileUnreadable, compare_with_local_reference
from pydocs_eval.bench_vm.leg_comparison_view import render_run_comparison
from pydocs_eval.bench_vm.local_reference import (
    LOCAL_REFERENCE_RESULTS_DIR,
    LocalReferenceRefused,
    local_reference_argv,
    require_local_reference_kind,
)
from pydocs_eval.bench_vm.run_control import LOCAL_RUNS_RESULTS_DIR
from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec


class LocalReferenceVerb(StrEnum):
    """The local-reference verbs, as the command line spells them."""

    LOCAL_COMMAND = "local-command"
    COMPARE = "compare"


LOCAL_REFERENCE_VERBS = frozenset(LocalReferenceVerb)
_EXIT_OK = 0
_EXIT_REFUSED = 1
_SPEC_HELP = "the run spec, e.g. infra/bench-vm/runs/x.yaml"


def add_local_reference_verbs(verbs: argparse._SubParsersAction) -> None:
    local = verbs.add_parser(
        LocalReferenceVerb.LOCAL_COMMAND,
        help="print the runner command for an uninterrupted local run of a retrieval spec",
    )
    local.add_argument("spec", type=Path, help=_SPEC_HELP)
    local.add_argument(
        "--results-dir",
        type=Path,
        default=LOCAL_REFERENCE_RESULTS_DIR,
        help=f"where <run id>/ lands (default {LOCAL_REFERENCE_RESULTS_DIR}, git-ignored)",
    )
    compare = verbs.add_parser(
        LocalReferenceVerb.COMPARE,
        help="compare a pulled run's final aggregates with the local reference run's",
    )
    compare.add_argument("spec", type=Path, help=_SPEC_HELP)
    compare.add_argument(
        "--pulled-results-dir",
        type=Path,
        default=LOCAL_RUNS_RESULTS_DIR,
        help=f"where pull put <run id>/ (default {LOCAL_RUNS_RESULTS_DIR})",
    )
    compare.add_argument(
        "--local-results-dir",
        type=Path,
        default=LOCAL_REFERENCE_RESULTS_DIR,
        help=f"where local-command put <run id>/ (default {LOCAL_REFERENCE_RESULTS_DIR})",
    )


def local_reference_verb(args: argparse.Namespace) -> int:
    """Run ``local-command`` or ``compare`` and return the exit code."""
    try:
        return _VERB_HANDLERS[LocalReferenceVerb(args.verb)](args)
    except RunSpecError as exc:
        for error in exc.errors:
            print(f"{args.spec}: {error.render()}", file=sys.stderr)
        return _EXIT_REFUSED
    except LocalReferenceRefused as exc:
        print(f"{args.spec}: {args.verb} refused: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except LegFileUnreadable as exc:
        print(f"{args.spec}: {args.verb} failed: {exc}", file=sys.stderr)
        return _EXIT_REFUSED


def _local_command(args: argparse.Namespace) -> int:
    print(shlex.join(local_reference_argv(load_run_spec(args.spec), args.results_dir)))
    return _EXIT_OK


def _compare(args: argparse.Namespace) -> int:
    spec_file = load_run_spec(args.spec)
    require_local_reference_kind(spec_file)
    comparison = compare_with_local_reference(
        spec_file.run_id, args.pulled_results_dir, args.local_results_dir
    )
    for failure in comparison.failures:
        print(f"{args.spec}: {args.verb}: {failure}", file=sys.stderr)
    print(render_run_comparison(comparison))
    return _EXIT_OK if comparison.identical else _EXIT_REFUSED


_VERB_HANDLERS: dict[LocalReferenceVerb, Callable[[argparse.Namespace], int]] = {
    LocalReferenceVerb.LOCAL_COMMAND: _local_command,
    LocalReferenceVerb.COMPARE: _compare,
}
