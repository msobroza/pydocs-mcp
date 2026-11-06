"""What ``_fixture_child.py`` writes into the caches when ``$FIXTURE_CACHES`` asks it to, the way the producers do.

- ``bench-index``: the eval suite's own bench index cache (``_bench_cache``),
  found through the ``HOME`` the supervisor gives the child. One entry under
  ``$FIXTURE_INDEX_KEY`` is reserved, built and promoted; its SQLite stays open
  in WAL mode with a row the main file does not hold yet until the entry's
  archive appears in the fake bucket (``$FIXTURE_GCS_ROOT``), so the push
  happens while the database is live; at most ``$FIXTURE_PUSH_WAIT_S`` seconds,
  for a test where the push must not happen. A second entry is reserved and built but
  never promoted: an entry still being built.
- ``before-after``: one corpus whose bundle is assembled and marked
  (``before_after_bundle_marker``), its served copy then written to as a serve
  child would; and one bundle copied without its marker (still assembling).
  The workspace is the child's own ``--workspace``.
- ``downloads``: a dataset download, a judge answer, embedder weights, and a
  download still in flight.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

from pydocs_eval import _bench_cache
from pydocs_eval.campaign.before_after_bundle_marker import (
    AssembledBundle,
    write_bundle_assembled_marker,
)

UNPROMOTED_KEY = "f" * 64
INDEX_ROW = "written while the entry was live"
CORPUS_SLUG = "psf__black@aaaaaaaaaaaa"
SCOPE = "0123456789abcdef"
MARKED_BUNDLE = f"{CORPUS_SLUG}@{SCOPE}"
UNMARKED_BUNDLE = f"openai__openai-python@bbbbbbbbbbbb@{SCOPE}"
CANONICAL_DB_NAME = "black_0123456789.db"
SERVED_BYTES = b"bytes a serve child wrote"
_DEFAULT_PUSH_WAIT_S = "15"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _new_index(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE chunks (text TEXT)")
    connection.commit()
    return connection


def _wait_for_object(name_suffix: str) -> None:
    gcs_root = Path(os.environ["FIXTURE_GCS_ROOT"])
    wait_s = float(os.environ.get("FIXTURE_PUSH_WAIT_S", _DEFAULT_PUSH_WAIT_S))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if any(gcs_root.rglob(f"*{name_suffix}")):
            return
        time.sleep(0.05)


def produce_bench_index() -> None:
    key = os.environ["FIXTURE_INDEX_KEY"]
    unfinished = _bench_cache.reserve(UNPROMOTED_KEY)
    _write(unfinished / "index.sqlite", b"half-built")
    if _bench_cache.lookup(key) is not None:  # restored from the bucket: nothing to build
        return
    build = _bench_cache.reserve(key)
    _new_index(build / "index.sqlite").close()
    _write(build / "index.tq", b"vectors")
    db = _bench_cache.commit(key, build)
    live = sqlite3.connect(db)
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO chunks VALUES (?)", (INDEX_ROW,))
    live.commit()
    _wait_for_object(f"{key}.tar")
    live.close()


def _workspace_root() -> Path:
    argv = sys.argv[1:]
    return Path(argv[argv.index("--workspace") + 1]) / "task-workspaces"


def produce_before_after() -> None:
    root = _workspace_root()
    _write(root / "sources" / CORPUS_SLUG / "a.py", b"NEEDLE = 1\n")
    canonical = root / "index-cache" / SCOPE / CANONICAL_DB_NAME
    _new_index(canonical).close()
    _write(canonical.with_suffix(".tq"), b"vectors")
    bundle = root / "bundles" / MARKED_BUNDLE
    _write(bundle / canonical.name, canonical.read_bytes())
    _write(bundle / canonical.with_suffix(".tq").name, b"vectors")
    write_bundle_assembled_marker(
        bundle,
        AssembledBundle(
            source_dir=f"sources/{CORPUS_SLUG}",
            index_files=(
                f"index-cache/{SCOPE}/{CANONICAL_DB_NAME}",
                f"index-cache/{SCOPE}/{canonical.with_suffix('.tq').name}",
            ),
        ),
    )
    _write(bundle / canonical.name, SERVED_BYTES)  # a serve child opens its copy read-write
    _write(root / "bundles" / UNMARKED_BUNDLE / "openai.db", b"half-copied")


def produce_downloads() -> None:
    home = Path.home()
    _write(home / ".cache" / "pydocs-mcp" / "ds1000" / "ds1000.jsonl", b'{"task": 1}\n')
    _write(home / ".cache" / "pydocs-mcp" / "ds1000" / "next.jsonl.incomplete", b"{")
    _write(_bench_cache.cache_root() / "jev" / ("c" * 64 + ".json"), b'{"verdict": "pass"}')
    _write(Path(os.environ["HF_HOME"]) / "hub" / "models--x" / "weights.bin", b"weights")
    _write(Path(os.environ["FASTEMBED_CACHE_PATH"]) / "model.onnx", b"onnx")


PRODUCERS = {
    "bench-index": produce_bench_index,
    "before-after": produce_before_after,
    "downloads": produce_downloads,
}


def produce_caches() -> None:
    for name in filter(None, os.environ.get("FIXTURE_CACHES", "").split(",")):
        PRODUCERS[name]()
