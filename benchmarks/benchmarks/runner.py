"""Deprecated runner stub — superseded by :mod:`benchmarks.eval.runner`.

The placeholder ``fake_project`` + ``dataset_gen`` flow this runner used to
orchestrate has been removed in favour of the RepoQA-SNF eval harness. This
shim only exists so the existing ``run-benchmarks`` console script keeps a
useful exit instead of an opaque ``ModuleNotFoundError`` after the cleanup.
"""
from __future__ import annotations

import sys

_MESSAGE = (
    "benchmarks.runner is superseded by the RepoQA + MLflow harness.\n"
    "Use: python -m benchmarks.eval.runner --help\n"
    "See benchmarks/README.md for install and usage instructions."
)


def main() -> int:
    print(_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    sys.exit(main())
