"""Smoke-test: resolve every ``pydocs_mcp.*`` import in benchmarks/.

CI runs only ``tests/``; benchmarks/ never gets exercised. A rename of an
internal module (``storage/wiring.py`` → ``storage/factories.py`` was the
real case) silently breaks the benchmark scripts. This script catches
that class of drift by AST-scanning each benchmark entry file, collecting
the ``import pydocs_mcp.X`` / ``from pydocs_mcp.X import Y`` statements,
and verifying each one resolves.

We do NOT exec the benchmark files — they import ``pandas``/``httpx``/
``rich`` which aren't installed in the test job. AST-level resolution is
surgical: it catches stale paths + missing attributes, nothing else.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys

# The eval code lives in two places since the pydocs-mcp-eval repackaging:
# operator scripts and the installable package. The old benchmarks/benchmarks/
# location is long gone — scanning it made this gate vacuous (0 files).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH_DIRS = (
    _REPO_ROOT / "benchmarks" / "scripts",
    _REPO_ROOT / "benchmarks" / "src" / "pydocs_eval",
)


def collect_pydocs_imports(py: pathlib.Path) -> list[tuple[str, str | None]]:
    """Return ``(module, attr_or_None)`` pairs for every pydocs_mcp import."""
    out: list[tuple[str, str | None]] = []
    tree = ast.parse(py.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("pydocs_mcp")
        ):
            for alias in node.names:
                out.append((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pydocs_mcp"):
                    out.append((alias.name, None))
    return out


def main() -> int:
    failed: list[str] = []
    checked = 0
    files = sorted({py for d in BENCH_DIRS for py in d.rglob("*.py")})
    for py in files:
        rel = py.relative_to(_REPO_ROOT)
        for module, attr in collect_pydocs_imports(py):
            checked += 1
            try:
                mod = importlib.import_module(module)
            except Exception as exc:  # broad on purpose — surfacing to operator
                failed.append(f"{rel}: import {module!r}: {exc}")
                continue
            if attr is not None and not hasattr(mod, attr):
                failed.append(f"{rel}: {module}.{attr} missing")

    if failed:
        print("Stale benchmark imports detected:", file=sys.stderr)
        for line in failed:
            print(f"  - {line}", file=sys.stderr)
        return 1
    if checked == 0:
        # The gate scanned nothing — that is a failure of the gate itself, not
        # a pass. This exact hole existed once (dead benchmarks/benchmarks dir).
        print(
            f"vacuous gate: 0 pydocs_mcp imports found under {len(files)} files — check BENCH_DIRS",
            file=sys.stderr,
        )
        return 1
    print(f"verified {checked} pydocs_mcp imports across {len(files)} benchmark files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
