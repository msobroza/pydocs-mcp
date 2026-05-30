# benchmarks/src/benchmarks/eval/_bench_cache.py
"""Per-(corpus, ingestion-config) index cache for the benchmark harness.

PydocsMcpSystem.index() builds a fresh SQLite per task; across a sweep
that re-indexes the same corpora 30 x N times. This cache keys an indexed
DB by (resolved corpus dir, ingestion_pipeline_hash) so each corpus is
indexed once per ingestion config and reused across tasks AND across
sweeps that share that ingestion pipeline. The entry is a directory so
the dense (.tq) / late-interaction (.plaid) sidecars — which
build_uow_factory derives from db_path.stem — travel with the .sqlite.
Lives under ~/.pydocs-mcp/bench/ (outside the repo). Toggle with
set_enabled() (the runner wires --bench-cache on|off).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_DB_FILENAME = "index.sqlite"

# Process-level toggle. Default on; the runner flips it from --bench-cache.
_ENABLED = True


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = value


def is_enabled() -> bool:
    return _ENABLED


def cache_root() -> Path:
    # Mirrors the shipped ~/.pydocs-mcp/{...}.db cache convention; outside the repo.
    return Path("~/.pydocs-mcp/bench").expanduser()


def make_key(corpus_dir: Path, config: AppConfig) -> str:
    corpus = str(Path(corpus_dir).resolve())
    ingestion_hash = config.compute_ingestion_pipeline_hash()
    raw = f"{corpus}\x00{ingestion_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


def entry_dir(key: str) -> Path:
    return cache_root() / key


def db_path_for(key: str) -> Path:
    return entry_dir(key) / _DB_FILENAME


def lookup(key: str) -> Path | None:
    db = db_path_for(key)
    try:
        if db.is_file() and db.stat().st_size > 0:
            return db
    except OSError:
        return None
    return None


def reserve(key: str) -> Path:
    """A fresh empty build dir for `key`, sibling to the final entry."""
    tmp = cache_root() / f"{key}.{os.getpid()}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def commit(key: str, build_dir: Path) -> Path:
    """Atomically promote a built dir to the final entry; return the db path.

    If another process already produced the entry (race), drop ours and
    use theirs — a duplicate build is idempotent for benchmark purposes.
    """
    final = entry_dir(key)
    if final.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
        return db_path_for(key)
    final.parent.mkdir(parents=True, exist_ok=True)
    build_dir.replace(final)  # atomic dir rename on the same filesystem
    return db_path_for(key)
