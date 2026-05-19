#!/usr/bin/env bash
# One-command entry to the RepoQA benchmark runner.
#
# Forwards every argument to ``python -m benchmarks.benchmarks.eval.runner``.
# Run from anywhere — resolves the repo root via BASH_SOURCE so a developer
# can invoke ``./scripts/run_repoqa.sh --help`` without first ``cd``-ing.
#
# WHY ``PYTHONPATH=benchmarks`` + the short ``benchmarks.eval.runner``
# module path: the shipped eval package uses ``from benchmarks.eval...``
# absolute imports throughout (see ``serialization.py``), so the
# ``benchmarks/`` directory must sit on sys.path. Adding it via
# PYTHONPATH and ``cd``-ing into the repo root keeps the invocation
# launchable from any cwd.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
# WHY ``python3`` (not ``python``): macOS ships only ``python3`` on PATH
# by default; ``python`` exists only inside an active venv. Calling the
# binary by its versioned name keeps the script callable from a fresh
# shell that has not activated any environment.
PYTHONPATH="benchmarks${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m benchmarks.eval.runner "$@"
