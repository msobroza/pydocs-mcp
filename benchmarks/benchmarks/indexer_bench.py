"""Time pydocs-mcp indexing for a fake project and its dependencies.

We call the pydocs-mcp internals directly to get per-package timings.
This avoids subprocess overhead and lets us capture structured results.
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.db import open_index_database
from pydocs_mcp.indexer import index_dependencies, index_project_source
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service


@dataclass
class IndexResult:
    """Timing result for a single indexing target."""
    target: str          # package name or '__project__'
    elapsed_s: float     # wall-clock seconds
    chunks: int = 0
    symbols: int = 0
    error: str = ""


async def _run_indexing_async(
    db_path: Path,
    project_root: Path,
    dep_names: list[str],
    use_inspect: bool,
) -> list[IndexResult]:
    """Run project + per-dep indexing under a single event loop.

    The async indexer functions own their own loop boundary, so we construct
    one IndexingService here and reuse it across targets — matching what the
    CLI does in ``__main__._run_indexing_phase``.
    """
    results: list[IndexResult] = []
    service = build_sqlite_indexing_service(db_path)

    # --- Project ---
    t0 = time.perf_counter()
    try:
        await index_project_source(service, project_root)
        elapsed = time.perf_counter() - t0
        with sqlite3.connect(db_path) as conn:
            chunks = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE package='__project__'"
            ).fetchone()[0]
            symbols = conn.execute(
                "SELECT COUNT(*) FROM module_members WHERE package='__project__'"
            ).fetchone()[0]
        results.append(IndexResult("__project__", elapsed, chunks, symbols))
    except Exception as exc:
        results.append(IndexResult("__project__", time.perf_counter() - t0, error=str(exc)))

    # --- Each dep individually for per-package timing ---
    for dep in dep_names:
        t0 = time.perf_counter()
        try:
            await index_dependencies(
                service, [dep],
                depth=1, workers=1,
                use_inspect=use_inspect,
            )
            elapsed = time.perf_counter() - t0
            norm = dep.lower().replace("-", "_")
            with sqlite3.connect(db_path) as conn:
                chunks = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE package=?", (norm,)
                ).fetchone()[0]
                symbols = conn.execute(
                    "SELECT COUNT(*) FROM module_members WHERE package=?", (norm,)
                ).fetchone()[0]
            results.append(IndexResult(dep, elapsed, chunks, symbols))
        except Exception as exc:
            results.append(IndexResult(dep, time.perf_counter() - t0, error=str(exc)))

    await service.chunk_store.rebuild_index()
    return results


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
        workers: Retained for API compatibility; per-dep timing uses 1 worker
            to keep measurements clean (kept in the signature so ``runner.py``
            can pass its configured ``--workers`` without changes).

    Returns:
        List of IndexResult, one per target (project first, then each dep).
    """
    del workers  # per-dep loop uses workers=1 for clean single-package timing
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        # Ensure schema exists before repositories issue queries.
        open_index_database(db_path).close()
        return asyncio.run(
            _run_indexing_async(db_path, project_root, dep_names, use_inspect)
        )
