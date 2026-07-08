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


def test_serve_index_watch_accept_gpu_flag() -> None:
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    for cmd in ("serve", "index", "watch"):
        args = parser.parse_args([cmd, ".", "--gpu"])
        assert args.gpu is True
        args_default = parser.parse_args([cmd, "."])
        assert args_default.gpu is False


def test_cli_verbose_shows_traceback(tmp_path: Path) -> None:
    # Trigger a controlled failure by pointing at a nonexistent cache dir.
    result = _run_cli(
        "search",
        "x",
        "--cache-dir",
        str(tmp_path / "nonexistent"),
        "-v",
    )
    # CQ-2: assert the trigger condition explicitly. A bare ``if rc != 0``
    # silently passes if the CLI ever stops failing on the trigger, hiding
    # any regression in the stderr-content contract this test pins.
    assert result.returncode != 0, (
        f"expected nonzero exit; got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Traceback" in result.stderr


def test_cli_no_verbose_omits_traceback(tmp_path: Path) -> None:
    result = _run_cli(
        "search",
        "x",
        "--cache-dir",
        str(tmp_path / "nonexistent"),
    )
    # CQ-2: see test_cli_verbose_shows_traceback for the rationale.
    assert result.returncode != 0, (
        f"expected nonzero exit; got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Traceback" not in result.stderr
    assert "re-run with --verbose" in result.stderr


def test_cli_index_nonexistent_config_path_fails_loud(tmp_path: Path) -> None:
    """A typo'd ``--config`` path must fail with a non-zero exit and name the
    offending path, not silently index against shipped defaults.

    Regression guard: ``AppConfig.settings_customise_sources`` drops any user
    layer that fails ``.exists()`` with no diagnostic, so a mistyped overlay
    used to index/serve/watch with the wrong embedder / pipeline / capture
    toggles and exit 0. ``AppConfig.load`` now rejects a missing explicit
    path outright, so this reaches the shared ``Error: <msg>`` policy.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    missing_config = tmp_path / "typo.yaml"
    result = _run_cli(
        "--config",
        str(missing_config),
        "index",
        str(project_dir),
        "--cache-dir",
        str(tmp_path / "cache"),
    )
    assert result.returncode != 0, (
        f"expected nonzero exit; got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "typo.yaml" in result.stderr
    assert "Error:" in result.stderr


def test_cmd_serve_does_not_wrap_run_in_to_thread() -> None:
    """CQ-1: ``pydocs-mcp serve``'s blocking MCP ``run(...)`` must execute
    on the main thread so Python's default SIGINT handler reaches it.

    ``mcp.run()`` (the blocking call ``server.run`` ultimately invokes)
    calls ``anyio.run(self.run_stdio_async)`` internally, starting a new
    event loop. If ``run(...)`` is wrapped in ``asyncio.to_thread(...)``,
    that loop spins up on a worker thread — but Python only delivers
    SIGINT to the main thread, and ``asyncio.to_thread`` cannot cancel a
    running thread. Net effect: Ctrl+C against ``pydocs-mcp serve`` is
    swallowed and the user has to ``kill -9`` the process.

    This is a static-analysis pin (no subprocess / signal plumbing
    needed): the offending pattern is one substring inside one function.
    """
    import inspect

    from pydocs_mcp.__main__ import _cmd_serve

    src = inspect.getsource(_cmd_serve)
    assert "to_thread(run" not in src and "to_thread( run" not in src, (
        "_cmd_serve must call run(...) on the main thread to preserve "
        "SIGINT delivery; got source:\n" + src
    )


# ---------- shared error-policy helpers (_report_cli_failure / _run_blocking) ----------


def test_run_blocking_keyboard_interrupt_is_success() -> None:
    """Ctrl+C against serve/watch is a graceful shutdown — exit code 0."""
    from pydocs_mcp.__main__ import _run_blocking

    def boom() -> None:
        raise KeyboardInterrupt

    assert _run_blocking(boom, verbose=False) == 0


def test_run_blocking_exception_routes_through_shared_policy(capsys) -> None:
    from pydocs_mcp.__main__ import _run_blocking

    def boom() -> None:
        raise RuntimeError("kaput")

    assert _run_blocking(boom, verbose=False) == 1
    err = capsys.readouterr().err
    assert "Error: kaput" in err
    assert "re-run with --verbose" in err


def test_report_cli_failure_verbose_prints_traceback(capsys) -> None:
    from pydocs_mcp.__main__ import _report_cli_failure

    try:
        raise RuntimeError("kaput")
    except RuntimeError as exc:
        code = _report_cli_failure(exc, verbose=True)
    assert code == 1
    err = capsys.readouterr().err
    assert "Error: kaput" in err
    assert "Traceback" in err


def test_error_policy_lives_in_exactly_one_function() -> None:
    """Anti-drift pin: the user-facing hint string must appear exactly once
    in the module source — inside _report_cli_failure. A second occurrence
    means a copy-pasted except-body grew back."""
    import pathlib

    import pydocs_mcp.__main__ as main_mod

    src = pathlib.Path(main_mod.__file__).read_text()
    assert src.count("re-run with --verbose to see the traceback") == 1
