"""``python -m pydocs_eval.bench_vm check SPEC...``: validate run specs on the laptop.

Prints ``<spec>: <run id>`` for each valid spec on stdout and one
``<spec>: <field>: got …, expected …`` line per problem on stderr. Exits 1 when
any spec is refused, 0 otherwise. It reads only the files it is given: no
credentials, no network, no git — ``make bench-vm-check`` runs it over every
committed spec in CI.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydocs_eval.bench_vm.run_spec import RunSpecError
from pydocs_eval.bench_vm.run_spec_file import load_run_spec

_EXIT_OK = 0
_EXIT_REFUSED = 1


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
    return parser


def check_run_specs(paths: Sequence[Path]) -> int:
    """Validate every spec in ``paths``, report each, and return the exit code."""
    refused = False
    for path in paths:
        try:
            loaded = load_run_spec(path)
        except RunSpecError as exc:
            refused = True
            for error in exc.errors:
                print(f"{path}: {error.render()}", file=sys.stderr)
            continue
        print(f"{path}: {loaded.run_id}")
    return _EXIT_REFUSED if refused else _EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_bench_vm_parser().parse_args(argv)
    return check_run_specs(args.specs)


if __name__ == "__main__":
    sys.exit(main())
