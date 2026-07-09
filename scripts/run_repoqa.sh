#!/usr/bin/env bash
# One-command entry to the RepoQA benchmark runner.
#
# Forwards every argument to ``python -m pydocs_eval.runner``.
# Run from anywhere — resolves the repo root via BASH_SOURCE so a developer
# can invoke ``./scripts/run_repoqa.sh --help`` without first ``cd``-ing.
#
# WHY ``PYTHONPATH=benchmarks/src`` + the short ``pydocs_eval.runner``
# module path: the shipped eval package uses ``from pydocs_eval...``
# absolute imports throughout (see ``serialization.py``), and the package
# lives under ``benchmarks/src/pydocs_eval/`` (PyPA src-layout). Adding
# ``benchmarks/src`` to sys.path via PYTHONPATH lets the absolute imports
# resolve without requiring an editable install.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
# WHY ``python3`` (not ``python``): macOS ships only ``python3`` on PATH
# by default; ``python`` exists only inside an active venv. Calling the
# binary by its versioned name keeps the script callable from a fresh
# shell that has not activated any environment.
PYTHONPATH="benchmarks/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m pydocs_eval.runner "$@"
