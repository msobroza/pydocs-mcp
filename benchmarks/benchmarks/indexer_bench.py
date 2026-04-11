"""Time pydocs-mcp indexing for a fake project and its dependencies.

We call the pydocs-mcp internals directly to get per-package timings.
This avoids subprocess overhead and lets us capture structured results.
"""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.db import open_db, rebuild_fts
from pydocs_mcp.indexer import index_project, index_deps


@dataclass
class IndexResult:
    """Timing result for a single indexing target."""
    target: str          # package name or '__project__'
    elapsed_s: float     # wall-clock seconds
    chunks: int = 0
    symbols: int = 0
    error: str = ""


def run_indexing_benchmark(
    project_root: Path,
    dep_names: list[str],
    use_inspect: bool = False,
    workers: int = 2,
) -> list[IndexResult]:
    """Index *project_root* and each dep in *dep_names*, returning timing rows.

    Args:
        project_root: Path to the fake (or real) project to index.
        dep_names: Dependency package names to index after the project.
        use_inspect: If True, use import+inspect mode (slower, richer).
        workers: ThreadPoolExecutor workers for dep indexing.

    Returns:
        List of IndexResult, one per target (project first, then each dep).
    """
    results: list[IndexResult] = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        conn = open_db(db_path)

        # --- Project ---
        t0 = time.perf_counter()
        try:
            index_project(conn, project_root)
            elapsed = time.perf_counter() - t0
            chunks = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE pkg='__project__'"
            ).fetchone()[0]
            symbols = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE pkg='__project__'"
            ).fetchone()[0]
            results.append(IndexResult("__project__", elapsed, chunks, symbols))
        except Exception as exc:
            results.append(IndexResult("__project__", time.perf_counter() - t0, error=str(exc)))

        # --- Each dep individually for per-package timing ---
        for dep in dep_names:
            t0 = time.perf_counter()
            try:
                index_deps(
                    conn, [dep],
                    depth=1, workers=1,
                    use_inspect=use_inspect,
                )
                elapsed = time.perf_counter() - t0
                norm = dep.lower().replace("-", "_")
                chunks = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE pkg=?", (norm,)
                ).fetchone()[0]
                symbols = conn.execute(
                    "SELECT COUNT(*) FROM symbols WHERE pkg=?", (norm,)
                ).fetchone()[0]
                results.append(IndexResult(dep, elapsed, chunks, symbols))
            except Exception as exc:
                results.append(IndexResult(dep, time.perf_counter() - t0, error=str(exc)))

        rebuild_fts(conn)
        conn.close()

    return results
