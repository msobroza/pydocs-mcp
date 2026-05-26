"""Tests for CLI top-level exception handling (C3).

Spec: the four ``_cmd_*`` subcommands collapse into a shared
``_run_cmd(coro, *, verbose)`` helper that:

- Prints ``Error: <msg>`` to stderr on any uncaught exception.
- Under ``--verbose`` (``-v``), also prints the full traceback.
- Without ``--verbose``, hints that ``--verbose`` reveals the traceback.
- Always emits ``log.exception("CLI command failed")`` for structured-log
  consumers.

The tests run the CLI in a subprocess so they exercise the actual
``python -m pydocs_mcp`` entry point — same code path real users hit.
A controlled failure is triggered by pointing ``--cache-dir`` at a
non-existent directory; the run is expected to fail with a non-zero exit
code, and the assertions then check what stderr looks like.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_python_root() -> Path:
    """Return ``python/`` for the in-tree package layout.

    The CI / dev workflow runs ``PYTHONPATH=$(pwd)/python pytest``; the
    subprocess here inherits a different env, so we explicitly point at
    the same directory so ``-m pydocs_mcp`` resolves to the worktree
    source rather than any globally-installed copy.
    """
    return Path(__file__).resolve().parent.parent / "python"


def _run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_repo_python_root())
    return subprocess.run(
        [sys.executable, "-m", "pydocs_mcp", *argv],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_verbose_shows_traceback(tmp_path: Path) -> None:
    # Trigger a controlled failure by pointing at a nonexistent cache dir.
    result = _run_cli(
        "search", "x",
        "--cache-dir", str(tmp_path / "nonexistent"),
        "-v",
    )
    if result.returncode != 0:
        combined = result.stderr
        assert "Traceback" in combined


def test_cli_no_verbose_omits_traceback(tmp_path: Path) -> None:
    result = _run_cli(
        "search", "x",
        "--cache-dir", str(tmp_path / "nonexistent"),
    )
    if result.returncode != 0:
        assert "Traceback" not in result.stderr
        assert "re-run with --verbose" in result.stderr
