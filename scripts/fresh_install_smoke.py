"""Fresh-install smoke test: index a tiny project, then talk MCP to ``serve``.

WHY: CI installs only from uv.lock, so a break in a freshly resolved dependency
set is invisible to it — mcp 2.x removed ``mcp.server.fastmcp`` and a fresh
``pip install pydocs-mcp`` crashed ``serve`` while every locked job stayed
green. This drives the INSTALLED package end to end (console script, index,
real stdio MCP handshake, one search) for the nightly fresh-install job and
the release pre-publish gate. Stdlib + the installed package only.

Usage: ``python scripts/fresh_install_smoke.py [--with-agent] [--timeout 120]``
(exit 0 on success, 1 with a ``FRESH-INSTALL SMOKE FAILED`` line otherwise).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# The frozen nine-tool surface (docs/tool-contracts.md).
EXPECTED_TOOLS = frozenset(
    {"get_overview", "search_codebase", "get_symbol", "get_context", "get_references"}
    | {"get_why", "grep", "glob", "read_file"}
)
SMOKE_QUERY = "fibonacci"
DEFAULT_TIMEOUT_S = 120.0
AGENT_MODULE = "pydocs_mcp.harness.ask_your_docs.agent"
SMOKE_MODULE_SOURCE = '''"""Number helpers for the pydocs-mcp fresh-install smoke test."""


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number, computed iteratively."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
'''


class SmokeFailure(Exception):
    """A smoke stage failed; the message is what the operator sees."""


def write_smoke_project(root: Path) -> Path:
    """Create ``root/project`` holding one documented module; return its path."""
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "numbers_demo.py").write_text(SMOKE_MODULE_SOURCE, encoding="utf-8")
    return project


def find_pydocs_cli(scripts_dir: str | None = None) -> str:
    """Locate this interpreter's ``pydocs-mcp`` console script, e.g. ``/venv/bin/pydocs-mcp``.

    WHY no PATH fallback: a stray ``pydocs-mcp`` elsewhere on PATH would let the
    gate pass against a different install than the one under test.
    """
    where = scripts_dir or sysconfig.get_path("scripts")
    found = shutil.which("pydocs-mcp", path=where)
    if found is None:
        raise SmokeFailure(
            f"pydocs-mcp console script not found in {where!r} (interpreter scripts dir)"
        )
    return found


def build_cli_args(subcommand: str, project: Path, cache: Path) -> list[str]:
    """Argument list for ``pydocs-mcp <subcommand>`` over the smoke project, deps skipped."""
    return [subcommand, str(project), "--skip-deps", "--cache-dir", str(cache)]


def find_missing_tools(names: Iterable[str]) -> list[str]:
    """Return the expected MCP tool names absent from ``names``, sorted."""
    return sorted(EXPECTED_TOOLS - set(names))


def extract_text(blocks: Iterable[Any]) -> str:
    """Join the ``text`` of every text content block of an MCP tool result."""
    return "\n".join(b.text for b in blocks if getattr(b, "type", None) == "text")


def check_search_text(text: str) -> None:
    """Fail unless the search answer is non-empty and mentions the smoke symbol."""
    if not text.strip():
        raise SmokeFailure("search_codebase returned no text")
    if SMOKE_QUERY not in text.lower():
        raise SmokeFailure(f"search_codebase text lacks {SMOKE_QUERY!r}: {text[:300]!r}")


def run_index(cli: str, project: Path, cache: Path, timeout: float) -> None:
    """Run ``pydocs-mcp index`` on the smoke project; raise on non-zero exit."""
    # S603: argv is built from our own constants and temp paths, never user input.
    proc = subprocess.run(  # noqa: S603
        [cli, *build_cli_args("index", project, cache)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise SmokeFailure(f"index exited {proc.returncode}:\n{proc.stderr[-2000:]}")


async def mcp_round_trip(cli: str, project: Path, cache: Path) -> None:
    """Initialize, list the nine tools and run one search over real MCP stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # WHY env=os.environ: the SDK default env drops TMPDIR / model-cache vars,
    # which would make the server re-download the embedder the index step used.
    params = StdioServerParameters(
        command=cli, args=build_cli_args("serve", project, cache), env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        missing = find_missing_tools(t.name for t in (await session.list_tools()).tools)
        if missing:
            raise SmokeFailure(f"list_tools is missing {missing}")
        result = await session.call_tool("search_codebase", {"query": SMOKE_QUERY})
        text = extract_text(result.content)
        if result.isError:
            raise SmokeFailure(f"search_codebase returned an error: {text[:500]!r}")
        check_search_text(text)


def check_agent_import() -> None:
    """Import the ask-your-docs agent — the module an mcp major bump broke."""
    try:
        importlib.import_module(AGENT_MODULE)
    except Exception as exc:  # broad on purpose: any import failure is the signal
        raise SmokeFailure(f"import {AGENT_MODULE} failed: {exc!r}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``--with-agent`` and ``--timeout`` (seconds, per stage)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--with-agent", action="store_true", help=f"also import {AGENT_MODULE}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    return parser.parse_args(argv)


def run_stage(label: str, action: Any) -> None:
    """Run one smoke stage and print how long it took."""
    started = time.monotonic()
    action()
    print(f"[smoke] {label}: ok ({time.monotonic() - started:.1f}s)", flush=True)


def run_smoke(args: argparse.Namespace, workdir: Path) -> None:
    """Run every stage in order; any failure raises."""
    cli, project, cache = find_pydocs_cli(), write_smoke_project(workdir), workdir / "cache"
    run_stage("index", lambda: run_index(cli, project, cache, args.timeout))
    handshake = mcp_round_trip(cli, project, cache)
    run_stage("mcp stdio", lambda: asyncio.run(asyncio.wait_for(handshake, args.timeout)))
    if args.with_agent:
        run_stage("agent import", check_agent_import)


def root_cause(exc: BaseException) -> BaseException:
    """Unwrap single-path exception groups (the MCP client's anyio task groups)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: 0 when every stage passes, 1 with a clear message otherwise."""
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="pydocs-smoke-") as tmp:
        try:
            run_smoke(args, Path(tmp))
        except Exception as exc:  # broad on purpose: report, never traceback-only
            cause = root_cause(exc)
            if isinstance(cause, TimeoutError):
                cause = SmokeFailure(f"stage exceeded {args.timeout}s")
            print(f"FRESH-INSTALL SMOKE FAILED: {type(cause).__name__}: {cause}", file=sys.stderr)
            return 1
    print("[smoke] fresh-install smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
