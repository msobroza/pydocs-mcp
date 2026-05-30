# Benchmark Per-Sample Cached DB — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the benchmark harness index each corpus once per ingestion-config (reused across tasks AND across sweeps), instead of building a fresh tmp SQLite per task, without changing the numbers a sweep produces.

**Architecture:** A small process-level cache module (`_bench_cache.py`) maps `(resolved_corpus_dir, ingestion_pipeline_hash) → cache entry directory` under `~/.pydocs-mcp/bench/`. `PydocsMcpSystem.index()` consults it: on a hit it points `_db_path` at the cached `.sqlite` and skips indexing; on a miss it indexes into a `.tmp` dir then atomically renames the dir into place (so the `.tq`/`.plaid` sidecars travel together). `teardown()` deletes only non-cached tmp DBs. A `--bench-cache on|off` runner flag (default `on`) toggles it; a `bench_cache` CLI lists/evicts.

**Tech Stack:** Python 3.11, stdlib only (`hashlib`, `os`, `shutil`, `pathlib`), pytest. Confined to `benchmarks/` — no `pydocs_mcp` production code changes.

**Spec:** `docs/superpowers/specs/2026-05-30-bench-per-sample-cache-design.md`

**Deviations from spec (deliberate, discovered during planning — fold back into spec):**
- **D1 key refinement:** key on `config.compute_ingestion_pipeline_hash()` (ingestion only), NOT the full config. The indexed DB depends only on ingestion + embedder + corpus; the retrieval pipeline (BM25 vs tree vs LI) never changes DB *content*. So configs that share an ingestion pipeline (e.g. `repoqa_bm25` + `repoqa_tree` — neither overrides the `pipelines.ingestion` route, so both resolve to the same default ingestion hash) share one cached DB. Bigger win; AC3 reframed accordingly.
- **D2/D3 storage refinement:** cache entry is a **directory** `~/.pydocs-mcp/bench/<key>/` holding `index.sqlite` (+ `index.tq` / `index.plaid` sidecars), built in `<key>.<pid>.tmp/` and promoted with an atomic `os.replace` of the *directory*. The spec's flat single-`.db` sketch would orphan the LI `.plaid` and dense `.tq` sidecars (`build_uow_factory` derives them from `db_path.stem`), breaking LI/dense on a cache hit.

**Out of scope (validated BM25/tree only):** the benchmark's `_do_index` uses `build_sqlite_indexing_service` (SQLite-only UoW), so `uow.vectors`/`uow.multi_vectors` are Null no-ops — NO `.tq`/`.plaid` is written today, and dense/LI configs retrieve against absent sidecars (a pre-existing wiring bug, tracked in its own PR). This cache is verified end-to-end on BM25 + tree; the directory shape (Task 2) is forward-compatible so dense/LI gain caching for free once that bug is fixed. Task 2's `test_reserve_then_commit_promotes_atomically` covers spec AC12 with a FAKE `index.plaid` (proves the dir-move preserves arbitrary sidecars without needing real LI).

---

### Task 1: Cache module — key + paths + enable flag

**Files:**
- Create: `benchmarks/src/benchmarks/eval/_bench_cache.py`
- Test: `benchmarks/tests/eval/test_bench_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/eval/test_bench_cache.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache


class _FakeConfig:
    # Stand-in for AppConfig: only compute_ingestion_pipeline_hash is read.
    def __init__(self, h: str) -> None:
        self._h = h

    def compute_ingestion_pipeline_hash(self) -> str:
        return self._h


def test_make_key_is_deterministic(tmp_path: Path) -> None:
    cfg = _FakeConfig("abc")
    k1 = _bench_cache.make_key(tmp_path, cfg)
    k2 = _bench_cache.make_key(tmp_path, cfg)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hexdigest


def test_make_key_varies_with_corpus_and_ingestion(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert _bench_cache.make_key(a, _FakeConfig("h")) != _bench_cache.make_key(b, _FakeConfig("h"))
    assert _bench_cache.make_key(a, _FakeConfig("h1")) != _bench_cache.make_key(a, _FakeConfig("h2"))


def test_make_key_resolves_corpus_path(tmp_path: Path) -> None:
    # A relative-ish / unresolved path and its resolved form share a key.
    sub = tmp_path / "x"
    sub.mkdir()
    via_dotdot = tmp_path / "x" / ".." / "x"
    assert _bench_cache.make_key(sub, _FakeConfig("h")) == _bench_cache.make_key(
        via_dotdot, _FakeConfig("h")
    )


def test_enabled_flag_roundtrips() -> None:
    original = _bench_cache.is_enabled()
    try:
        _bench_cache.set_enabled(False)
        assert _bench_cache.is_enabled() is False
        _bench_cache.set_enabled(True)
        assert _bench_cache.is_enabled() is True
    finally:
        _bench_cache.set_enabled(original)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmarks.eval._bench_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
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
    return Path(os.path.expanduser("~/.pydocs-mcp/bench"))


def make_key(corpus_dir: Path, config: AppConfig) -> str:
    corpus = str(Path(corpus_dir).resolve())
    ingestion_hash = config.compute_ingestion_pipeline_hash()
    raw = f"{corpus}\x00{ingestion_hash}".encode()
    return hashlib.sha256(raw).hexdigest()


def entry_dir(key: str) -> Path:
    return cache_root() / key


def db_path_for(key: str) -> Path:
    return entry_dir(key) / _DB_FILENAME
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/_bench_cache.py benchmarks/tests/eval/test_bench_cache.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): bench cache key + paths + enable flag"
```

---

### Task 2: Cache lookup / reserve / commit (atomic dir promote)

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/_bench_cache.py`
- Test: `benchmarks/tests/eval/test_bench_cache.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_lookup_miss_then_hit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    assert _bench_cache.lookup("deadbeef") is None
    # Simulate a built entry.
    d = _bench_cache.entry_dir("deadbeef")
    d.mkdir(parents=True)
    db = _bench_cache.db_path_for("deadbeef")
    db.write_text("not empty")
    assert _bench_cache.lookup("deadbeef") == db


def test_lookup_ignores_empty_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").touch()  # zero bytes
    assert _bench_cache.lookup("k") is None


def test_reserve_then_commit_promotes_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    build = _bench_cache.reserve("k")
    assert build.is_dir()
    # Write the db + a sidecar into the build dir.
    (build / "index.sqlite").write_text("db")
    (build / "index.plaid").write_text("plaid sidecar")
    db = _bench_cache.commit("k", build)
    assert db == _bench_cache.db_path_for("k")
    assert db.read_text() == "db"
    assert (_bench_cache.entry_dir("k") / "index.plaid").read_text() == "plaid sidecar"
    assert not build.exists()  # tmp consumed by the rename


def test_commit_loses_race_drops_tmp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    # Pre-create the final entry (another process won).
    final = _bench_cache.entry_dir("k")
    final.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("winner")
    build = _bench_cache.reserve("k")
    (build / "index.sqlite").write_text("loser")
    db = _bench_cache.commit("k", build)
    assert db.read_text() == "winner"  # winner kept
    assert not build.exists()  # loser dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: FAIL — `AttributeError: module 'benchmarks.eval._bench_cache' has no attribute 'lookup'`

- [ ] **Step 3: Write minimal implementation** (append to `_bench_cache.py`)

```python
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
    os.replace(build_dir, final)  # atomic dir rename on the same filesystem
    return db_path_for(key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/_bench_cache.py benchmarks/tests/eval/test_bench_cache.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): bench cache lookup + atomic dir reserve/commit"
```

---

### Task 3: `info()` + `evict()` cache operations

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/_bench_cache.py`
- Test: `benchmarks/tests/eval/test_bench_cache.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_evict_removes_everything(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")
    removed = _bench_cache.evict()
    assert removed == 1
    assert not _bench_cache.cache_root().exists() or not any(_bench_cache.cache_root().iterdir())


def test_evict_empty_cache_is_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    assert _bench_cache.evict() == 0


def test_info_lists_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db-bytes")
    rows = _bench_cache.info()
    assert len(rows) == 1
    assert rows[0]["key"] == "k"
    assert rows[0]["bytes"] >= len("db-bytes")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'evict'`

- [ ] **Step 3: Write minimal implementation** (append)

```python
def evict() -> int:
    """Remove every cache entry directory. Returns the count removed."""
    root = cache_root()
    if not root.exists():
        return 0
    count = 0
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            count += 1
        else:
            child.unlink(missing_ok=True)
    return count


def info() -> list[dict[str, object]]:
    """One row per cache entry: key, total bytes, db mtime (epoch)."""
    root = cache_root()
    rows: list[dict[str, object]] = []
    if not root.exists():
        return rows
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.endswith(".tmp"):
            continue
        total = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        db = child / _DB_FILENAME
        mtime = db.stat().st_mtime if db.is_file() else 0.0
        rows.append({"key": child.name, "bytes": total, "mtime": mtime})
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/_bench_cache.py benchmarks/tests/eval/test_bench_cache.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): bench cache info + evict"
```

---

### Task 4: Extract `_do_index` from `PydocsMcpSystem.index()` (pure refactor)

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py:62-146`

Splitting the heavy indexing body into a helper so Task 5 can call it from both the cache-hit-miss and the no-cache branch without duplication. NO behavior change in this task.

**Subclass coverage (review M1):** `_do_index` and the Task 5 cache branch MUST live in the **base** `PydocsMcpSystem`. The tree variants `PydocsTreeOnlySystem` / `PydocsTreeParallelSystem` (`pydocs.py:292-344`) override only `index()` to swap the config, then call `super().index(...)` — so they inherit `_do_index` + caching automatically, and their override config feeds `make_key`. Do NOT duplicate indexing logic into the subclasses, or they silently lose caching. The existing `test_pydocs_system.py` baseline (Step 1) exercises the base path; no subclass-specific test is needed since they delegate.

- [ ] **Step 1: Run the existing pydocs system tests to capture green baseline**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_pydocs_system.py -q`
Expected: PASS (record the count, e.g. "N passed")

- [ ] **Step 2: Refactor — extract `_do_index`**

In `pydocs.py`, replace the body of `index()` from the `uow_factory = build_sqlite_uow_factory(...)` line (currently line 96) through `await chunk_repo.rebuild_index()` (currently line 143) with a call, and move that body verbatim into a new method `_do_index`. The `index()` method keeps: the deferred imports, `await self.teardown()`, the `mkstemp` tmp-db creation, `open_index_database(...).close()`, then `await self._do_index(corpus_dir, config)`, then the `build_retrieval_context` + `build_chunk_pipeline_from_config` tail.

Result shape (the imports block at the top of `index()` stays unchanged):

```python
    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        from pydocs_mcp.db import open_index_database
        # ... (keep the other deferred imports that index() still needs:
        #      build_retrieval_context, build_chunk_pipeline_from_config) ...
        await self.teardown()

        fd, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self._db_path = Path(name)
        open_index_database(self._db_path).close()

        await self._do_index(corpus_dir, config)

        context = build_retrieval_context(self._db_path, config)
        self._pipeline = build_chunk_pipeline_from_config(config, context)

    async def _do_index(self, corpus_dir: Path, config: AppConfig) -> None:
        """Index `corpus_dir` into the SQLite at ``self._db_path`` (already
        created/empty). Builds the ingestion pipeline + embedder, runs
        ProjectIndexer, rebuilds the FTS index. No tmp-file or pipeline
        lifecycle here — the caller owns ``self._db_path`` and the search
        pipeline."""
        from pydocs_mcp.application import ProjectIndexer
        from pydocs_mcp.db import build_connection_provider
        from pydocs_mcp.extraction import (
            AstMemberExtractor,
            PipelineChunkExtractor,
            StaticDependencyResolver,
            build_ingestion_pipeline,
        )
        from pydocs_mcp.extraction.strategies.embedders import build_embedder
        from pydocs_mcp.storage.factories import (
            build_sqlite_indexing_service,
            build_sqlite_uow_factory,
        )
        from pydocs_mcp.storage.sqlite import SqliteChunkRepository

        uow_factory = build_sqlite_uow_factory(self._db_path)
        indexing_service = build_sqlite_indexing_service(self._db_path)
        embedder = build_embedder(config.embedding)
        ingestion_pipeline = build_ingestion_pipeline(
            config,
            embedder=embedder,
            uow_factory=uow_factory,
            pipeline_hash=config.compute_ingestion_pipeline_hash(),
        )
        indexer = ProjectIndexer(
            indexing_service=indexing_service,
            dependency_resolver=StaticDependencyResolver(),
            chunk_extractor=PipelineChunkExtractor(pipeline=ingestion_pipeline),
            member_extractor=AstMemberExtractor(),
            uow_factory=uow_factory,
        )
        await indexer.index_project(
            corpus_dir, force=True, include_project_source=True, workers=1,
        )
        chunk_repo = SqliteChunkRepository(provider=build_connection_provider(self._db_path))
        await chunk_repo.rebuild_index()
```

(Keep the explanatory WHY comments from the original body on their lines.)

- [ ] **Step 3: Run the existing tests to verify no behavior change**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_pydocs_system.py -q`
Expected: PASS (same count as Step 1)

- [ ] **Step 4: Commit**

```bash
git add benchmarks/src/benchmarks/eval/systems/pydocs.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "refactor(benchmarks): extract _do_index from PydocsMcpSystem.index"
```

---

### Task 5: Cache-aware `index()` + cache-safe `teardown()`

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py` (add `_db_is_cached` field; cache branch in `index()`; guard `teardown()`)
- Test: `benchmarks/tests/eval/test_bench_cache_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/eval/test_bench_cache_integration.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache
from benchmarks.eval.systems.pydocs import PydocsMcpSystem


def _tiny_corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "mod.py").write_text("def hello():\n    return 1\n")
    (d / "pyproject.toml").write_text('[project]\nname="tiny"\nversion="0"\n')
    return d


@pytest.fixture
def _cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    monkeypatch.setattr(_bench_cache, "set_enabled", _bench_cache.set_enabled)
    _bench_cache.set_enabled(True)
    yield
    _bench_cache.set_enabled(True)


async def test_index_once_reused_across_instances(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    calls = {"n": 0}
    real_do_index = PydocsMcpSystem._do_index

    async def counting_do_index(self, corpus_dir, cfg):
        calls["n"] += 1
        await real_do_index(self, corpus_dir, cfg)

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", counting_do_index)

    a = PydocsMcpSystem()
    await a.index(corpus, config)
    await a.teardown()

    b = PydocsMcpSystem()
    await b.index(corpus, config)  # same (corpus, ingestion hash) -> cache hit
    await b.teardown()

    assert calls["n"] == 1  # indexed once, second was a cache hit


async def test_cache_off_indexes_every_time(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    _bench_cache.set_enabled(False)
    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    calls = {"n": 0}
    real_do_index = PydocsMcpSystem._do_index

    async def counting_do_index(self, corpus_dir, cfg):
        calls["n"] += 1
        await real_do_index(self, corpus_dir, cfg)

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", counting_do_index)

    for _ in range(2):
        s = PydocsMcpSystem()
        await s.index(corpus, config)
        await s.teardown()

    assert calls["n"] == 2  # no cache -> indexed each time


async def test_teardown_keeps_cached_db(tmp_path, monkeypatch, _cache_in_tmp) -> None:
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()
    s = PydocsMcpSystem()
    await s.index(corpus, config)
    key = _bench_cache.make_key(corpus, config)
    cached_db = _bench_cache.db_path_for(key)
    assert cached_db.is_file()
    await s.teardown()
    assert cached_db.is_file()  # teardown must NOT delete the cache


async def test_failed_cold_index_leaves_no_orphan_build_dir(
    tmp_path, monkeypatch, _cache_in_tmp
) -> None:
    # AC16 / review C1: if _do_index raises on a MISS, the half-built
    # <key>.<pid>.tmp/ dir must not survive, and no entry is promoted.
    from pydocs_mcp.retrieval.config import AppConfig

    corpus = _tiny_corpus(tmp_path)
    config = AppConfig.load()

    async def boom(self, corpus_dir, cfg):
        raise RuntimeError("indexing blew up")

    monkeypatch.setattr(PydocsMcpSystem, "_do_index", boom)

    s = PydocsMcpSystem()
    with pytest.raises(RuntimeError, match="blew up"):
        await s.index(corpus, config)
    await s.teardown()  # must be safe even after the failed index

    root = _bench_cache.cache_root()
    leftovers = list(root.iterdir()) if root.exists() else []
    assert leftovers == []  # no .tmp build dir, no promoted entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache_integration.py -q`
Expected: FAIL — `test_index_once_...` asserts `calls["n"] == 1` but gets `2` (no cache yet); `test_teardown_keeps_cached_db` fails because `index()` doesn't write to the cache dir.

- [ ] **Step 3: Write minimal implementation**

In `pydocs.py`, add two fields to `PydocsMcpSystem` — `_db_is_cached`
controls teardown (don't delete a cached DB); `_was_cache_hit` records
whether the LAST `index()` was served from cache, so the runner can
exclude warm tasks from `indexing_seconds` (spec D9 / AC14):

```python
    _db_is_cached: bool = field(default=False, init=False, repr=False)
    _was_cache_hit: bool = field(default=False, init=False, repr=False)
```

And expose the read-only property the runner reads (spec D9):

```python
    @property
    def was_cache_hit(self) -> bool:
        """True iff the most recent index() returned a cached DB without
        indexing. The runner uses this to skip the indexing_seconds
        observation on warm tasks (a ~0 s cache lookup is not an
        indexing-time measurement — spec D9)."""
        return self._was_cache_hit
```

Add the cache import + `shutil` at module top (NOT deferred — stdlib-light;
`shutil` is needed for the C1 leak-safe cleanup in the MISS branch):

```python
import shutil

from .. import _bench_cache
```
(`pydocs.py` already imports `os` and `tempfile` at module top.)

Replace `index()` so the DB is sourced from the cache when enabled:

```python
    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        from pydocs_mcp.db import open_index_database
        from pydocs_mcp.retrieval.config import build_chunk_pipeline_from_config
        from pydocs_mcp.retrieval.factories import build_retrieval_context

        await self.teardown()

        if _bench_cache.is_enabled():
            key = _bench_cache.make_key(corpus_dir, config)
            cached = _bench_cache.lookup(key)
            if cached is not None:
                # HIT: reuse the indexed DB (+ its sidecars) as-is.
                self._db_path = cached
                self._db_is_cached = True
                self._was_cache_hit = True  # spec D9: exclude from indexing_seconds
            else:
                # MISS: index into a tmp dir, then atomically promote so the
                # .tq/.plaid sidecars travel with the .sqlite.
                build_dir = _bench_cache.reserve(key)
                self._db_path = build_dir / "index.sqlite"
                self._was_cache_hit = False  # cold task: a real indexing measurement
                # WHY (spec C1/AC16): mark _db_is_cached only AFTER a
                # successful commit. If _do_index raises, teardown() would
                # skip a "cached" path and leak the half-built tmp dir, so
                # clean the build dir here and re-raise.
                self._db_is_cached = False
                open_index_database(self._db_path).close()
                try:
                    await self._do_index(corpus_dir, config)
                except BaseException:
                    shutil.rmtree(build_dir, ignore_errors=True)
                    self._db_path = None
                    raise
                self._db_path = _bench_cache.commit(key, build_dir)
                self._db_is_cached = True
        else:
            fd, name = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            self._db_path = Path(name)
            self._db_is_cached = False
            self._was_cache_hit = False  # --bench-cache off: every task is cold
            open_index_database(self._db_path).close()
            await self._do_index(corpus_dir, config)

        context = build_retrieval_context(self._db_path, config)
        self._pipeline = build_chunk_pipeline_from_config(config, context)
```

Guard `teardown()` so cached entries survive (leave `_was_cache_hit`
intact — the runner reads it AFTER `index()` and BEFORE the next
`teardown()`, so resetting it here is unnecessary and would mask the
just-completed task's hit/miss state):

```python
    async def teardown(self) -> None:
        path = self._db_path
        if path is None:
            return
        if not self._db_is_cached:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            for suffix in ("-wal", "-shm"):
                sib = path.with_name(path.name + suffix)
                try:
                    sib.unlink()
                except FileNotFoundError:
                    pass
        self._db_path = None
        self._pipeline = None
        self._db_is_cached = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache_integration.py benchmarks/tests/eval/test_pydocs_system.py -q`
Expected: PASS (3 new + the existing pydocs system tests)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/systems/pydocs.py benchmarks/tests/eval/test_bench_cache_integration.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): cache-aware index() + cache-safe teardown()"
```

---

### Task 5b: Runner excludes cache-hit tasks from `indexing_seconds` (D9 / AC14)

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py:202-220` (the timing block in `run_sweep`)
- Test: `benchmarks/tests/eval/test_runner_timing_cache_hit.py`

A cache hit makes `index()` a ~0 s lookup; recording that as
`indexing_seconds` would report "indexing takes 0.0 s" (spec D9). Skip
the `indexing_seconds` observation when the system reports a cache hit;
always record `search_seconds`.

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/eval/test_runner_timing_cache_hit.py
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.runner import run_sweep
from benchmarks.eval.serialization import (
    dataset_registry,
    system_registry,
    tracker_registry,
)
from benchmarks.eval.systems.base_system import RetrievedItem
from benchmarks.eval.trackers.base_tracker import RunHandle

# Module-level sink the fake tracker appends to (run_sweep builds the
# tracker via the registry with no handle to our list otherwise).
_LOGGED: list[tuple[str, float]] = []


@tracker_registry.register("fake-timing-tracker")
@dataclass
class _FakeTracker:
    name: str = "fake-timing-tracker"

    def open_run(self, **_kw) -> RunHandle:
        return RunHandle(tracker_name=self.name, raw=None)

    def log_metric(self, handle, name, value, step=None) -> None:  # noqa: ARG002
        _LOGGED.append((name, value))

    def close_run(self, handle, status) -> None:  # noqa: ARG002
        pass


@dataset_registry.register("fake-timing-dataset")
@dataclass
class _FakeDataset:
    name: str = "fake-timing-dataset"
    revision: str = "v0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for i in range(3):  # 3 tasks: cold, warm, warm
            yield EvalTask(
                task_id=f"t{i}",
                query="q",
                gold=GoldAnswer(ast_body="def f(): return 1"),
                corpus_source=lambda: Path(),
                metadata={},
            )


@system_registry.register("fake-timing-system")
@dataclass
class _FakeSystem:
    name: str = "fake-timing-system"
    _n: int = field(default=0, init=False)
    _hit: bool = field(default=False, init=False)

    async def index(self, corpus_dir: Path, config) -> None:  # noqa: ARG002
        # First task cold (miss), the rest warm (hit) — the cache shape
        # this whole feature produces on a single corpus.
        self._hit = self._n > 0
        self._n += 1

    @property
    def was_cache_hit(self) -> bool:
        return self._hit

    async def search(self, query: str, limit: int):  # noqa: ARG002
        return (RetrievedItem(rank=1, text="def g(): return 2", source_path="p"),)

    async def teardown(self) -> None:
        return None


async def test_cache_hit_tasks_emit_no_indexing_seconds(tmp_path) -> None:
    _LOGGED.clear()
    await run_sweep(
        systems=("fake-timing-system",),
        config_paths=(tmp_path / "cfg.yaml",),  # stem is the only thing read
        dataset_name="fake-timing-dataset",
        tracker_names=("fake-timing-tracker",),
        metric_specs=("recall@1",),
    )
    idx = [v for n, v in _LOGGED if n == "indexing_seconds"]
    srch = [v for n, v in _LOGGED if n == "search_seconds"]
    assert len(idx) == 1   # only the cold task recorded indexing time
    assert len(srch) == 3  # search recorded every task


async def test_all_warm_leg_emits_no_indexing_aggregate(tmp_path) -> None:
    # I2/AC15: when EVERY task is a hit, the empty indexing_seconds series
    # must not emit a 0.0 aggregate row.
    @system_registry.register("fake-all-warm-system")
    @dataclass
    class _AllWarm(_FakeSystem):
        name: str = "fake-all-warm-system"

        async def index(self, corpus_dir: Path, config) -> None:  # noqa: ARG002
            self._hit = True  # every task a hit

    _LOGGED.clear()
    await run_sweep(
        systems=("fake-all-warm-system",),
        config_paths=(tmp_path / "cfg.yaml",),
        dataset_name="fake-timing-dataset",
        tracker_names=("fake-timing-tracker",),
        metric_specs=("recall@1",),
    )
    assert not any(n == "indexing_seconds_p50" for n, _ in _LOGGED)  # row omitted
    assert any(n == "search_seconds_p50" for n, _ in _LOGGED)  # search row present
```

(The fakes register into the real registries — the established pattern in
`benchmarks/tests/eval/test_runner_gold_injection.py`. The fake system
exposes `index` / `search` / `was_cache_hit` / `teardown`; it is NOT a
`HasGoldResolver`, so `_resolve_and_inject` is a no-op and the gold's
`ast_body` drives `recall@1`. The assertion — `indexing_seconds` count ==
cold-task count — is the AC14 contract.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_timing_cache_hit.py -q`
Expected: FAIL — `indexing_seconds` recorded 3 times (the runner doesn't check `was_cache_hit` yet).

- [ ] **Step 3: Write minimal implementation**

In `run_sweep` (`runner.py`), the timing block currently reads:

```python
                    t0 = time.perf_counter()
                    await system.index(dir_, config)
                    index_secs = time.perf_counter() - t0

                    t1 = time.perf_counter()
                    retrieved = await system.search(task.query, limit=10)
                    search_secs = time.perf_counter() - t1

                    latency_values["indexing_seconds"].append(index_secs)
                    latency_values["search_seconds"].append(search_secs)
                    for h, tracker in zip(handles, trackers):
                        tracker.log_metric(h, "indexing_seconds", index_secs, step=count)
                        tracker.log_metric(h, "search_seconds", search_secs, step=count)
```

Change it to gate the indexing observation on a non-cache-hit
(`getattr` keeps systems without the property working — they default to
"not a hit" → always recorded, preserving today's behaviour):

```python
                    t0 = time.perf_counter()
                    await system.index(dir_, config)
                    index_secs = time.perf_counter() - t0

                    t1 = time.perf_counter()
                    retrieved = await system.search(task.query, limit=10)
                    search_secs = time.perf_counter() - t1

                    # Spec D9: a cache HIT makes index() a ~0 s lookup, not
                    # an indexing measurement. Record indexing_seconds only
                    # for cold tasks; search_seconds always (search is
                    # identical hit or miss). Systems without was_cache_hit
                    # default to False -> always recorded (unchanged).
                    cache_hit = getattr(system, "was_cache_hit", False)
                    if not cache_hit:
                        latency_values["indexing_seconds"].append(index_secs)
                    latency_values["search_seconds"].append(search_secs)
                    for h, tracker in zip(handles, trackers):
                        if not cache_hit:
                            tracker.log_metric(h, "indexing_seconds", index_secs, step=count)
                        tracker.log_metric(h, "search_seconds", search_secs, step=count)
```

- [ ] **Step 3b: Omit the aggregate row for an empty latency series (I2 / AC15)**

The per-task skip is not enough: when EVERY task in a leg is a cache hit,
`latency_values["indexing_seconds"]` ends up empty, and the aggregation
loop (`runner.py:267-276`) still calls `percentile([])` → `0.0` and logs
`indexing_seconds_p50/p95/p99 = 0.0` — the same "0.0 s indexing"
corruption, relocated to the aggregate. Gate the latency aggregation on a
non-empty series. The block currently reads:

```python
            for latency_key in LATENCY_KEYS:
                values = latency_values[latency_key]
                p50 = percentile(values, 0.5)
                p95 = percentile(values, 0.95)
                p99 = percentile(values, 0.99)
                aggregates[latency_key] = (p50, p95, p99)
                for h, tracker in zip(handles, trackers):
                    tracker.log_metric(h, f"{latency_key}_p50", p50, step=None)
                    tracker.log_metric(h, f"{latency_key}_p95", p95, step=None)
                    tracker.log_metric(h, f"{latency_key}_p99", p99, step=None)
```

Change it to skip an empty series entirely (an all-warm leg performed no
indexing, so it should have NO indexing-time row rather than a misleading
0.0):

```python
            for latency_key in LATENCY_KEYS:
                values = latency_values[latency_key]
                # Spec I2/AC15: an all-warm leg leaves indexing_seconds
                # empty (every task a cache hit skipped its observation).
                # percentile([]) is 0.0, so emitting here would report
                # "0.0 s indexing" — omit the row instead.
                if not values:
                    continue
                p50 = percentile(values, 0.5)
                p95 = percentile(values, 0.95)
                p99 = percentile(values, 0.99)
                aggregates[latency_key] = (p50, p95, p99)
                for h, tracker in zip(handles, trackers):
                    tracker.log_metric(h, f"{latency_key}_p50", p50, step=None)
                    tracker.log_metric(h, f"{latency_key}_p95", p95, step=None)
                    tracker.log_metric(h, f"{latency_key}_p99", p99, step=None)
```

Add a test to `test_runner_timing_cache_hit.py` asserting that an
all-hits leg logs NO `indexing_seconds_p50` metric while a leg with at
least one cold task does.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_timing_cache_hit.py -q`
Expected: PASS — `indexing_seconds` count == 1, `search_seconds` count == 3; all-hits leg emits no `indexing_seconds_p50`.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py benchmarks/tests/eval/test_runner_timing_cache_hit.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): exclude cache-hit tasks from indexing_seconds (D9 + empty-series guard)"
```

---

### Task 6: `--bench-cache on|off` runner flag

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (argparse in `_build_arg_parser`; set flag in `main()`)
- Test: `benchmarks/tests/eval/test_runner_bench_cache_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/eval/test_runner_bench_cache_flag.py
from __future__ import annotations

from benchmarks.eval import _bench_cache
from benchmarks.eval.runner import _build_arg_parser


def test_flag_defaults_on() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.bench_cache == "on"


def test_flag_accepts_off() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml", "--bench-cache", "off"])
    assert args.bench_cache == "off"


def test_set_enabled_maps_off(monkeypatch) -> None:
    # Helper the runner uses to translate the flag into the module toggle.
    from benchmarks.eval.runner import _apply_bench_cache_flag

    original = _bench_cache.is_enabled()
    try:
        _apply_bench_cache_flag("off")
        assert _bench_cache.is_enabled() is False
        _apply_bench_cache_flag("on")
        assert _bench_cache.is_enabled() is True
    finally:
        _bench_cache.set_enabled(original)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_bench_cache_flag.py -q`
Expected: FAIL — `argument --bench-cache: ...` unrecognized / `ImportError: cannot import name '_apply_bench_cache_flag'`

- [ ] **Step 3: Write minimal implementation**

In `runner.py`, add to `_build_arg_parser()` (next to the other `parser.add_argument` calls):

```python
    parser.add_argument(
        "--bench-cache",
        choices=["on", "off"],
        default="on",
        help=(
            "Reuse a per-(corpus, ingestion-hash) indexed DB across tasks and "
            "sweeps (default on). 'off' rebuilds a fresh tmp DB per task — use "
            "to reproduce pre-cache numbers exactly."
        ),
    )
```

Add the import + helper near the top of `runner.py`:

```python
from . import _bench_cache


def _apply_bench_cache_flag(value: str) -> None:
    _bench_cache.set_enabled(value == "on")
```

In `main()`, after `args = parser.parse_args()` and before launching the sweep:

```python
    _apply_bench_cache_flag(args.bench_cache)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_bench_cache_flag.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py benchmarks/tests/eval/test_runner_bench_cache_flag.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): --bench-cache on|off runner flag"
```

---

### Task 6b: `--bench-cache-cleanup` — wipe the cache after the sweep (D8)

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (`--bench-cache-cleanup` flag; `finally` cleanup in `main()`)
- Test: `benchmarks/tests/eval/test_runner_bench_cache_flag.py`

The "run my experiments, then free the disk" path. Orthogonal to `--bench-cache on|off` (use-a-cache vs clean-up-after are independent concerns), so the cleanup runs in a `finally` and fires even if the sweep raised.

- [ ] **Step 1: Write the failing test** (append to `test_runner_bench_cache_flag.py`)

```python
def test_cleanup_flag_defaults_false() -> None:
    args = _build_arg_parser().parse_args(["--configs", "x.yaml"])
    assert args.bench_cache_cleanup is False


def test_cleanup_flag_sets_true() -> None:
    args = _build_arg_parser().parse_args(
        ["--configs", "x.yaml", "--bench-cache-cleanup"]
    )
    assert args.bench_cache_cleanup is True


def test_maybe_cleanup_evicts_when_enabled(tmp_path, monkeypatch) -> None:
    from benchmarks.eval.runner import _maybe_cleanup_bench_cache

    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")

    _maybe_cleanup_bench_cache(enabled=True)
    assert _bench_cache.lookup("k") is None  # wiped


def test_maybe_cleanup_noop_when_disabled(tmp_path, monkeypatch) -> None:
    from benchmarks.eval.runner import _maybe_cleanup_bench_cache

    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")

    _maybe_cleanup_bench_cache(enabled=False)
    assert _bench_cache.lookup("k") is not None  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_bench_cache_flag.py -q`
Expected: FAIL — `argument --bench-cache-cleanup: ...` unrecognized / `ImportError: cannot import name '_maybe_cleanup_bench_cache'`

- [ ] **Step 3: Write minimal implementation**

In `runner.py`, add to `_build_arg_parser()` (next to `--bench-cache`):

```python
    parser.add_argument(
        "--bench-cache-cleanup",
        action="store_true",
        help=(
            "After the sweep finishes, evict the ENTIRE index cache at "
            "~/.pydocs-mcp/bench/ (run experiments, then free the disk). "
            "Runs even if the sweep raises. Independent of --bench-cache: "
            "'off --bench-cache-cleanup' caches nothing this run but still "
            "clears any stale cache. Do NOT use while a concurrent sweep "
            "shares the cache."
        ),
    )
```

Add the helper near `_apply_bench_cache_flag`:

```python
def _maybe_cleanup_bench_cache(*, enabled: bool) -> None:
    if enabled:
        _bench_cache.evict()
```

Wrap the sweep launch in `main()` so cleanup always runs:

```python
    _apply_bench_cache_flag(args.bench_cache)
    try:
        # ... existing sweep launch (asyncio.run(run_sweep(...)) etc.) ...
    finally:
        _maybe_cleanup_bench_cache(enabled=args.bench_cache_cleanup)
```

(Implementer note: keep the existing `main()` body inside the `try`; the
only additions are the `try:`/`finally:` lines and the cleanup call.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_runner_bench_cache_flag.py -q`
Expected: PASS (7 passed — 3 from Task 6 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py benchmarks/tests/eval/test_runner_bench_cache_flag.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): --bench-cache-cleanup wipes cache after the sweep"
```

---

### Task 7: `bench_cache` CLI (`info` / `evict`)

**Files:**
- Create: `benchmarks/src/benchmarks/eval/bench_cache.py`
- Test: `benchmarks/tests/eval/test_bench_cache_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/eval/test_bench_cache_cli.py
from __future__ import annotations

from benchmarks.eval import _bench_cache
from benchmarks.eval.bench_cache import main


def test_cli_info_empty(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    rc = main(["info"])
    assert rc == 0
    assert "0 entries" in capsys.readouterr().out


def test_cli_evict(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    d = _bench_cache.entry_dir("k")
    d.mkdir(parents=True)
    _bench_cache.db_path_for("k").write_text("db")
    rc = main(["evict"])
    assert rc == 0
    assert "evicted 1" in capsys.readouterr().out.lower()
    assert _bench_cache.lookup("k") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'benchmarks.eval.bench_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# benchmarks/src/benchmarks/eval/bench_cache.py
"""CLI for the benchmark index cache: `python -m benchmarks.eval.bench_cache {info,evict}`."""

from __future__ import annotations

import argparse
import datetime as _dt

from . import _bench_cache


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.eval.bench_cache")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="list cached indexed DBs (read-only)")
    sub.add_parser("evict", help="remove every cached indexed DB")
    args = parser.parse_args(argv)

    if args.cmd == "info":
        rows = _bench_cache.info()
        print(f"{_bench_cache.cache_root()}: {len(rows)} entries")
        for r in rows:
            mb = int(r["bytes"]) / 1_048_576
            ts = _dt.datetime.fromtimestamp(float(r["mtime"])).isoformat(timespec="seconds")
            print(f"  {str(r['key'])[:12]}  {mb:7.1f} MB  {ts}")
        return 0

    if args.cmd == "evict":
        n = _bench_cache.evict()
        print(f"evicted {n} cache entr{'y' if n == 1 else 'ies'} from {_bench_cache.cache_root()}")
        return 0

    return 1  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache_cli.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/bench_cache.py benchmarks/tests/eval/test_bench_cache_cli.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "feat(benchmarks): bench_cache CLI (info / evict)"
```

---

### Task 8: Metric-fidelity guard — cache on == cache off (AC2)

**Files:**
- Test: `benchmarks/tests/eval/test_bench_cache_fidelity.py`

Proves the cache changes *speed*, not *scores* — the entire justification for per-sample cache over a single shared DB.

- [ ] **Step 1: Write the failing test (red until the cache exists; here it's a guard that must stay green)**

```python
# benchmarks/tests/eval/test_bench_cache_fidelity.py
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.eval import _bench_cache
from benchmarks.eval.systems.pydocs import PydocsMcpSystem


def _tiny_corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "mod.py").write_text(
        "def alpha():\n    return 'alpha body'\n\n\ndef beta():\n    return 'beta body'\n"
    )
    (d / "pyproject.toml").write_text('[project]\nname="tiny"\nversion="0"\n')
    return d


async def _search_texts(corpus: Path) -> list[str]:
    from pydocs_mcp.retrieval.config import AppConfig

    system = PydocsMcpSystem()
    await system.index(corpus, AppConfig.load())
    hits = await system.search("alpha", limit=5)
    await system.teardown()
    return [h.text for h in hits]


async def test_cache_on_matches_cache_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_bench_cache, "cache_root", lambda: tmp_path / "bench")
    corpus = _tiny_corpus(tmp_path)

    _bench_cache.set_enabled(False)
    try:
        off = await _search_texts(corpus)
    finally:
        _bench_cache.set_enabled(True)

    on_cold = await _search_texts(corpus)  # builds cache
    on_warm = await _search_texts(corpus)  # cache hit

    assert off == on_cold == on_warm
    _bench_cache.set_enabled(True)
```

- [ ] **Step 2: Run test to verify it passes (the cache is implemented by now)**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache_fidelity.py -q`
Expected: PASS (1 passed) — identical search output across off / cold / warm.

- [ ] **Step 3: Commit**

```bash
git add benchmarks/tests/eval/test_bench_cache_fidelity.py
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "test(benchmarks): bench cache fidelity guard (on == off)"
```

---

### Task 9: Docs — reproduce recipes + cache notes

**Files:**
- Modify: `benchmarks/README.md`
- Modify: `benchmarks/reports/small_test_eval/README.md` (NOTE: lives on the held PR #58 branch, not this one — see Step 1)

- [ ] **Step 1: Update `benchmarks/README.md`**

Add a short "Index cache" subsection near the run recipes:

```markdown
### Index cache (faster repeated sweeps)

Each `(corpus, ingestion-config)` is indexed once and reused across tasks
and across sweeps that share an ingestion pipeline. Controlled by
`--bench-cache on|off` (default `on`). The cache lives at
`~/.pydocs-mcp/bench/` (outside the repo).

    # inspect / clear the cache
    python -m benchmarks.eval.bench_cache info
    python -m benchmarks.eval.bench_cache evict

    # run all experiments, then free the disk (cache used during the run,
    # wiped when it finishes — even if the run errors)
    python -m benchmarks.eval.runner --bench-cache-cleanup ...

    # reproduce pre-cache numbers exactly
    python -m benchmarks.eval.runner --bench-cache off ...

The cache key folds the ingestion pipeline hash, so changing the embedder
or the ingestion YAML rebuilds automatically. A change to the corpus
*contents* under the same path is NOT auto-detected — run `bench_cache
evict` or `--bench-cache off` after editing a corpus in place.

`--bench-cache-cleanup` evicts the WHOLE cache at the end (not just this
run's entries) — don't pass it while a concurrent sweep shares the cache.

**Reading indexing time:** a cache HIT makes `index()` a ~0 s lookup, so
warm tasks record NO `indexing_seconds` (the metric would otherwise read
"0.0 s"). Take true indexing-time numbers from a COLD run — the first
sweep after `bench_cache evict`, or any `--bench-cache off` run. Warm
sweeps still give correct quality + `search_seconds`. The sweep is
sequential (one task at a time, no concurrency), so a cold run's timing
is uncontended.
```

- [ ] **Step 2: Audit README for jargon (repo rule)**

Run: `cd <worktree> && grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" benchmarks/README.md`
Expected: no output (clean)

- [ ] **Step 3: Commit**

```bash
git add benchmarks/README.md
git -c user.name="Max Raphael Sobroza Marques" -c user.email="max.raphael@gmail.com" \
  commit -m "docs(benchmarks): document the index cache + --bench-cache flag"
```

---

### Task 10: Full verification gauntlet

**Files:** none (verification only)

- [ ] **Step 1: Full benchmark suite**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/ -q`
Expected: all pass (prior 290 + the new tests).

- [ ] **Step 2: Ruff**

Run: `cd <worktree> && .venv/bin/python -m ruff check benchmarks/ && .venv/bin/python -m ruff format --check benchmarks/`
Expected: `All checks passed!` + `... already formatted`. If format fails, run `ruff format benchmarks/` and amend the relevant commit.

- [ ] **Step 3: mypy (production unaffected)**

Run: `cd <worktree> && .venv/bin/python -m mypy python/pydocs_mcp`
Expected: same as main (the 2 local-env `fast_plaid` notes if the extra is installed; otherwise 0). No NEW errors — this PR touches no `pydocs_mcp` code.

- [ ] **Step 4: `git status` clean after a test run (AC6 — gitignore)**

Run: `cd <worktree> && PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/eval/test_bench_cache.py -q && git status --porcelain`
Expected: empty output (no stray tracked artifacts; the `~/.pydocs-mcp/bench/` cache is outside the repo, and `benchmarks/.gitignore` already covers `results/jsonl/`).

- [ ] **Step 5: Empirical lift (number for the PR description)**

Run a real 2-task RepoQA BM25 + tree pair (same ingestion) and confirm the SECOND config's tasks are cache hits:

```bash
cd <worktree>
set -a; source /Users/msobroza/Projects/pyctx7-mcp/python/pydocs_mcp/.env; set +a
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_bm25.yaml --report /tmp/r1.md --limit 2
python -m benchmarks.eval.bench_cache info   # 2 entries expected
```

Record the index-time drop (cold vs warm) in the PR description. Not a CI gate.

---

## Self-Review

**1. Spec coverage:**
- D1 (cache key) → Task 1 (+ refinement: ingestion hash).
- D2 (cache dir under ~/.pydocs-mcp/bench) → Task 1/2 (+ refinement: directory entry).
- D3 (index() = cache lookup) → Task 5.
- D4 (--bench-cache on|off, default on) → Task 6.
- D5 (teardown only removes tmp) → Task 5.
- D6 (CLI info/evict) → Task 7.
- D7 (.gitignore) → already committed in the spec commit; AC6 verified Task 10 Step 4.
- D8 (--bench-cache-cleanup, wipe after sweep) → Task 6b.
- D9 (cache-hit excluded from indexing_seconds) → Task 5 (`was_cache_hit` property) + Task 5b (runner skip).
- AC1 index-once → Task 5. AC2 quality-fidelity → Task 8. AC3 invalidation-on-ingestion-change → Task 1 `test_make_key_varies_with_corpus_and_ingestion`. AC4 no-content-invalidation → documented Task 9; key is path+ingestion-hash by construction. AC5 teardown keeps cache → Task 5. AC6 gitignore → Task 10 Step 4. AC7 opt-out → Task 5 `test_cache_off_indexes_every_time`. AC8 CLI → Task 7. AC9 no prod code → Task 10 Step 3 + diff scope. AC10 empirical lift → Task 10 Step 5. AC11 green/ruff/mypy → Task 10. AC12 sidecar dir-move → Task 2 `test_reserve_then_commit_promotes_atomically` (fake `index.plaid`). AC13 cleanup-after-sweep → Task 6b. AC14 cache-hit-no-indexing_seconds → Task 5b `test_cache_hit_tasks_emit_no_indexing_seconds`. AC15 empty-series-no-aggregate-row → Task 5b Step 3b + `test_all_warm_leg_emits_no_indexing_aggregate`. AC16 failed-cold-index-no-orphan → Task 5 `test_failed_cold_index_leaves_no_orphan_build_dir`.

**2. Placeholder scan:** none — Task 5b's runner-timing test is now concrete (registers fakes into the real registries, the `test_runner_gold_injection.py` pattern). Every code step has complete, runnable code.

**3. Type consistency:** `make_key`/`lookup`/`reserve`/`commit`/`entry_dir`/`db_path_for`/`info`/`evict`/`is_enabled`/`set_enabled`/`cache_root` names used identically across tasks 1-8 and the CLI. `_db_is_cached` + `_was_cache_hit` field names + `was_cache_hit` property consistent in Task 5 / Task 5b. `_apply_bench_cache_flag` + `_maybe_cleanup_bench_cache` consistent in Task 6 / 6b. `_do_index` lives in the base class (Task 4) → tree subclasses inherit it (review M1).

**Note for the implementer:** `<worktree>` = `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/bench-per-sample-cache`; `.venv` = `/Users/msobroza/Projects/pyctx7-mcp/.venv`. The harness resets cwd between shell calls — always `cd <worktree> && ...` and use absolute paths for file writes.
