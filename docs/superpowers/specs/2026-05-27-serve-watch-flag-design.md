# `pydocs-mcp serve --watch` — design

**Status:** spec — ready for implementation planning
**Tracks:** developer-experience / serve-mode auto-reindex
**Related work:** chunk-level cache + atomic vector cleanup (shipped),
clean-architecture review fixups (shipped via PR #43). Reuses the
existing two-level cache (`packages.content_hash` xxh3 of
`(path, mtime)` pairs + `chunks.content_hash` SHA-256), so re-running
indexing on every filesystem event is cheap (~100 ms for no-change
runs).

---

## 1. Goal

Close the staleness gap during `pydocs-mcp serve`. Today, indexing
runs once at startup; file edits during the server's lifetime are
invisible until the user either restarts the server or runs
`pydocs-mcp index .` from another terminal. After this PR:

```bash
pydocs-mcp serve . --watch
# → starts MCP server + filesystem watcher
# → edits to *.py / *.md / *.ipynb under the project root re-trigger
#   indexing in the background, debounced
# → next MCP search/lookup query sees fresh data
```

The watcher is **opt-in**. Default behavior (no `--watch`) stays
identical to today: index once at startup, then loop the MCP server
unchanged.

## 2. Context

`pydocs_mcp.__main__._cmd_serve` (post-PR #43) runs in two phases:

1. **Phase 1 (async):** `_run_serve_indexing(args)` → routes through
   `_run_cmd` so the `--verbose`/traceback policy applies. Builds
   `IndexingService` via the composition root + calls
   `ProjectIndexer.index_project(...)`.
2. **Phase 2 (blocking main thread):** `run(db_path, ...)` → starts
   the FastMCP stdio server, blocking on Ctrl+C. Lives on the main
   thread so SIGINT is delivered cleanly (per Batch 1 CQ-1 fix).

`--watch` adds a third element: an **asyncio file-system watcher
task** that runs alongside Phase 2 and re-triggers Phase 1 on
debounced filesystem events. The MCP server keeps running on the main
thread; the watcher + reindex runs on the asyncio event loop in a
worker thread.

The two-level cache (`packages.content_hash` xxh3 over
`(path, mtime)` pairs + `chunks.content_hash` SHA-256) makes
re-running `_run_indexing()` cheap in the no-change case:

- Unchanged package: package-hash matches → skip in <10 ms (one
  `SELECT` + one xxh3 walk).
- Changed package: re-extract chunks → diff against persisted set
  → re-embed only added/changed chunks.

So **the cheapest correct behavior is "fire a full re-index on any
relevant filesystem event"** — no per-file granularity needed. The
existing cache machinery makes the brute-force option fast enough.

## 3. Locked-in decisions

These were settled before scoping and do not get relitigated during
implementation.

### Decision A — Watcher library: `watchdog`

`watchdog>=4.0,<6.0` — mature, ~150 KB, supports macOS FSEvents +
Linux inotify + Windows ReadDirectoryChangesW with one API. Battle-
tested (used by `pytest-watch`, `livereload`, `mkdocs serve`).

Alternative considered + rejected: `watchfiles` (Rust-backed, faster
events but newer + smaller community). May revisit if `watchdog`'s
polling fallback on macOS deep-tree edges proves problematic.
Documented as a future swap, not a v1 choice.

### Decision B — Opt-in via `--watch` CLI flag, NOT default-on

The CLI flag stays off by default. Reasons:

1. **Backward compat:** users running today's `pydocs-mcp serve .`
   get identical behavior.
2. **Dependency footprint:** `watchdog` is a new runtime dep. Users
   who never use `--watch` shouldn't pay the install cost.
3. **Resource discipline:** the watcher consumes a small amount of
   memory + one OS-level event handle. Headless / CI deployments
   that don't need live edits stay lean.

**Implication for packaging:** `watchdog` ships as a soft dep via
an extras group: `pip install pydocs-mcp[watch]`. Without the extras,
`pydocs-mcp serve --watch` exits with `ServiceUnavailableError`
pointing the user at the install hint. Documented in §6 Risks R3.

### Decision C — CLI flag, YAML tuning (per CLAUDE.md §"MCP API surface vs YAML configuration")

The single CLI flag `--watch` is allowed as it's a per-deployment
boolean toggle (client-driven, like `--cache-dir`). All tuning lives
in YAML:

```yaml
# defaults in python/pydocs_mcp/defaults/default_config.yaml
serve:
  watch:
    enabled: false                # overridden by --watch
    debounce_ms: 500              # coalesce edits within this window
    extensions: [".py", ".md", ".ipynb"]
    ignore_globs:
      - "**/__pycache__/**"
      - "**/.git/**"
      - "**/.venv/**"
      - "**/node_modules/**"
      - "**/.pytest_cache/**"
      - "**/*.pyc"
```

No new MCP tool parameters. The MCP surface stays at the fixed 2
tools (`search`, `lookup`) per CLAUDE.md §"MCP API surface vs YAML
configuration".

### Decision D — Reindex granularity: full project reindex on any event

When a relevant filesystem event fires, the watcher calls
`_run_indexing(args)` exactly as Phase 1 would. **No per-file or
per-package smart-targeting in v1.** The two-level cache handles
the "only the changed bits get work" optimization:

- All unchanged packages are skipped at package-hash level (<10 ms
  each).
- The changed `__project__` package's chunks are diff-merged; only
  added/changed chunks are re-embedded.

Targeted partial re-indexing would add complexity (which package
owns the changed file? what if the file moved between packages?)
for no measurable wall-clock win on the typical edit cycle. **YAGNI
explicit** — file a follow-up PR if real-world latency proves the
need.

### Decision E — Concurrency: `asyncio.Lock` around reindex; coalesce events

Only one reindex runs at a time. Algorithm:

1. Filesystem event fires (after `extensions` + `ignore_globs` filter).
2. Cancel any pending debounce timer + start a new one for
   `debounce_ms` from now.
3. When timer fires:
   - If a reindex is in flight (`_reindex_lock.locked()`): set a
     `_reindex_pending = True` flag and return (the in-flight reindex
     will trigger a follow-up).
   - Otherwise: acquire lock, run `_run_indexing()`, release lock.
     If `_reindex_pending` is True post-run: clear it + schedule a
     new reindex.

This guarantees:
- No two reindexes in flight simultaneously (the SQLite UoW would
  serialize them anyway, but explicit is better).
- Edits during a long reindex aren't lost — they trigger exactly one
  follow-up reindex.
- Burst edits (editor save-all, git checkout) coalesce to one
  reindex, not N.

### Decision F — Scope: project root only; NOT `site-packages/`

The watcher monitors the project directory (`args.project`), not
the installed dependency directories under `site-packages/`. Reasons:

- Dependencies change only on `pip install` / `pip uninstall` —
  rare, user-initiated events. The user can re-run `pydocs-mcp index .`
  after a `pip` operation.
- Watching `site-packages/` would multiply event volume by ~100× on
  a typical 50-dep project. Most of those events would be irrelevant
  (compile-cache churn, `pip` metadata writes).
- A future PR can add `--watch-deps` as an explicit opt-in if
  real-world demand emerges. Out of v1 scope.

### Decision G — Shutdown: graceful via existing `KeyboardInterrupt` handler

The CQ-1 fix in PR #43 already gave `_cmd_serve` a clean
`KeyboardInterrupt` exit path on Ctrl+C. The watcher task + reindex
loop register as `asyncio.Task` objects that get cancelled by the
same `KeyboardInterrupt` propagation. No new shutdown logic needed
beyond cancelling the watcher task before `run(...)` exits.

### Decision H — Authorship policy

Every commit on this branch is sole-authored by `msobroza`. No
`Co-Authored-By:` trailers. Standing global rule.

## 4. Scope

### 4.1 In scope (PR deliverables)

1. **`watchdog>=4.0,<6.0`** added to `pyproject.toml` under an extras
   group: `[project.optional-dependencies]` → `watch = ["watchdog>=4.0,<6.0"]`.

2. **`--watch` CLI flag** on the `serve` subparser only. Plumbed
   through `_cmd_serve` to a new `_run_watch_loop(args, db_path)`
   helper.

3. **`pydocs_mcp/serve/watcher.py`** — new module:
   - `FileWatcher` dataclass wrapping `watchdog.observers.Observer`
   - Async event-loop integration via an `asyncio.Queue` + watcher
     thread that puts events onto the queue
   - Filter + debounce + coalesce per Decision E
   - One method: `async def run_until_cancelled(self, on_change: Callable)`

4. **`AppConfig.serve.watch`** sub-model (pydantic) with the 4 keys
   from Decision C. CLI `--watch` overrides `enabled`. Cross-field
   validator: `debounce_ms` must be > 0 and < 60000.

5. **Composition root wiring** — `__main__._cmd_serve` Phase 2:
   - Without `--watch`: identical to today (`run(db_path, ...)` on
     main thread).
   - With `--watch`: spawn an asyncio task that runs the watcher,
     then call `run(db_path, ...)` on the main thread. On Ctrl+C
     (or any exception out of `run`), cancel the watcher task before
     exiting.

6. **Test suite extensions:**
   - `tests/test_watcher.py` — new file
   - `test_watcher_filters_unrelated_events` — `.pyc` and
     `__pycache__/` files don't trigger the callback
   - `test_watcher_debounces_burst_edits` — 3 events within 500 ms →
     1 callback
   - `test_watcher_coalesces_during_in_flight_reindex` — event
     during reindex → exactly 1 follow-up callback
   - `test_watcher_respects_extensions_yaml_override` — custom
     extensions list filters correctly
   - `test_cmd_serve_without_watch_unchanged` — no watcher task
     created when `--watch` not set

7. **README + DOCUMENTATION.md updates:**
   - README: one sentence + one code block showing `--watch` usage
   - DOCUMENTATION.md: new subsection under "Indexing" documenting
     the YAML knobs, the debounce + coalesce semantics, and the
     `pip install pydocs-mcp[watch]` requirement

8. **NEW: standalone `pydocs-mcp watch <project>` subcommand** —
   watcher loop only, no MCP server. Spec follow-up. Targets operators
   who want a fresh on-disk index for the CLI `search` / `lookup`
   commands (or an IDE-driven workflow) without keeping an idle
   FastMCP stdio process running. Reads the same `serve.watch.*` YAML
   knobs as `serve --watch`. Implementation shares a
   `_build_watcher_and_callback(args, watch_cfg)` helper with
   `_run_watch_loop` so both modes can only differ in whether they
   ALSO run an MCP server.

### 4.2 Out of scope

- Watching `site-packages/` for dependency changes (Decision F)
- Per-file or per-package partial reindex (Decision D)
- HTTP-based MCP transport variant (the watcher is stdio-MCP-only;
  HTTP/SSE transport, if it lands, gets its own watcher integration)
- Notifying connected MCP clients about a reindex (no event stream
  in MCP today; clients see fresh data on the next query)
- Watch mode for `pydocs-mcp index` (only `serve` gets `--watch` —
  `index` is a one-shot)
- Configurable polling vs native-event backend (Decision A —
  `watchdog` picks the best per-platform default; user override is a
  future YAML knob)

## 5. Domain components touched

| Component | Change |
|---|---|
| `pyproject.toml` | New `[project.optional-dependencies].watch` group |
| `python/pydocs_mcp/__main__.py` | `--watch` flag on serve subparser; `_run_watch_loop` helper |
| `python/pydocs_mcp/serve/__init__.py` | NEW (or extend existing) |
| `python/pydocs_mcp/serve/watcher.py` | NEW: `FileWatcher` dataclass + async loop |
| `python/pydocs_mcp/retrieval/config.py` | `ServeConfig` + `WatchConfig` pydantic sub-models on `AppConfig.serve` |
| `python/pydocs_mcp/defaults/default_config.yaml` | New `serve.watch.*` defaults |
| `tests/test_watcher.py` | NEW test file |
| `README.md` | One paragraph + code block |
| `DOCUMENTATION.md` | New "Live re-indexing" subsection |

Approximate LOC: **~280 production + ~200 tests + ~40 docs = ~520 total**.

## 6. Risks

### Risk R1 — `watchdog` cross-platform inconsistency

macOS FSEvents has known issues with very deeply nested directories
(>20 levels) and symlinked roots. Linux inotify has a per-user
`max_user_watches` limit (default 8192) that a giant monorepo could
hit. Windows ReadDirectoryChangesW caps event buffer size.

**Mitigation:** `watchdog` provides a `PollingObserver` fallback.
Document the failure mode (silent stale results) and add a YAML
opt-in: `serve.watch.backend: "native" | "polling"` (default
"native"). If `Observer.start()` throws, log a warning + fall back
to `PollingObserver` automatically. Add a per-platform smoke test
in `tests/test_watcher.py` skipped on incompatible OSes.

### Risk R2 — Reindex churn during git operations

A `git checkout`, `git rebase`, or `git stash pop` rewrites every
file in the tree → potentially thousands of events in <1 s. Even
with 500 ms debounce, a long-running git op could trigger multiple
back-to-back reindexes.

**Mitigation:** Decision E's coalesce-during-in-flight pattern
handles this — at most one queued reindex behind the active one.
Worst case: O(2) reindexes per git op, each ~100 ms for cached
unchanged packages. Acceptable.

Additional mitigation: ignore `.git/` events entirely (already in
Decision C default ignore list).

### Risk R3 — Missing dep → cryptic ImportError

Without the `watch` extras installed, `import watchdog` at module
load time would crash `pydocs-mcp serve --watch`.

**Mitigation:** lazy-import `watchdog` inside `serve/watcher.py`
under a `try/except ImportError` that raises
`ServiceUnavailableError` with the actionable message:

```
"--watch requires the 'watch' extras. Install via:
    pip install pydocs-mcp[watch]"
```

The default `pydocs-mcp serve` (no `--watch`) never imports
`watchdog`, so users who don't need the watcher pay zero cost.

### Risk R4 — Asyncio task lifecycle: orphaned watcher on crash

If `run(db_path, ...)` crashes (not `KeyboardInterrupt`), the
watcher task might be left dangling.

**Mitigation:** wrap the watcher spawn + `run()` call in a
`try/finally` that cancels the watcher task in the `finally` block.
Pin via test: simulate a crash inside `run()` and assert the
watcher's `Observer.stop()` was called.

### Risk R5 — Concurrent indexing collides with active MCP queries

A reindex acquires a SQLite write lock; concurrent MCP queries hold
read connections. SQLite serializes writers but allows concurrent
readers in WAL mode. If WAL isn't enabled, readers block.

**Mitigation:** verify WAL mode is the default for the index DB
(check `db.py:open_index_database`). If not, enable it OR document
the brief query-latency hit during reindex (likely <100 ms for
cached runs).

### Risk R6 — Test flakiness from real filesystem events

Tests that touch the real filesystem are slower and flakier than
unit tests with mocked `Observer`.

**Mitigation:** the test suite uses a `FakeObserver` that injects
events synchronously (no real `watchdog.Observer` thread). One
end-to-end smoke test with the real `Observer` runs against a
`tmp_path` fixture and uses `pytest.mark.slow` so it can be excluded
from fast CI runs.

### Risk R7 — Debounce interaction with editor "atomic save"

Many editors (VS Code, neovim with `:set backupcopy=auto`) save by
writing to a temp file, then renaming. This generates 2-3 events
per save: temp create, original delete, temp rename. The debounce
naturally collapses these into one reindex trigger, but if the
debounce window is too short (e.g., 100 ms), a slow disk might
fire reindexes for the intermediate states.

**Mitigation:** default debounce 500 ms is comfortably wider than
any editor's atomic-save sequence. Document the trade-off in the
YAML doc string for `serve.watch.debounce_ms`.

## 7. Acceptance criteria

1. **AC-1 — `pydocs-mcp serve . --watch` starts MCP server + filesystem
   watcher**, both logged at startup.

2. **AC-2 — Editing a `.py` file under the project root triggers
   reindex within `debounce_ms + ~50 ms`** (measured by a test that
   writes a file, polls the DB for the updated content_hash).

3. **AC-3 — Filesystem events under `__pycache__/`, `.git/`, `.venv/`,
   or matching `*.pyc` do NOT trigger reindex.** Pin via test with
   the test-only `FakeObserver`.

4. **AC-4 — N edits within `debounce_ms` result in exactly 1 reindex.**

5. **AC-5 — An edit during an in-flight reindex schedules exactly
   one follow-up reindex.**

6. **AC-6 — Ctrl+C cleanly shuts down both the watcher (Observer.stop
   called) AND the MCP server.** Pin via test that sends SIGINT and
   asserts both shut down within 1 s.

7. **AC-7 — Without `--watch`, `pydocs-mcp serve` behavior is
   byte-identical to pre-PR.** Test: run with + without `--watch`
   against the same fixture; assert identical DB state after one
   query.

8. **AC-8 — YAML `serve.watch.{enabled,debounce_ms,extensions,ignore_globs}`
   overrides apply; CLI `--watch` overrides `enabled`.**

9. **AC-9 — `watchdog` lives behind the `[watch]` extras group;**
   `pip install pydocs-mcp` (without extras) → `pydocs-mcp serve
   --watch` raises `ServiceUnavailableError` with install hint;
   `pip install pydocs-mcp[watch]` → works.

10. **AC-10 — Authorship audit clean** — every commit on this branch
    sole-authored by `msobroza`, no `Co-Authored-By` trailers.

11. **AC-11 — Full test suite green.** `pytest -q` passes at the
    locked baseline + new tests; ruff + benchmark suite unchanged.

12. **AC-12 — Docs updated.** README has the one-paragraph mention;
    DOCUMENTATION.md has the new subsection.

## 8. Open items for implementation planning

These do not block this spec; the implementer resolves them in the
plan:

- **O1 — Watcher thread → asyncio bridge pattern.** `watchdog` runs
  its `Observer` in a native thread. Need to bridge events into the
  asyncio event loop without dropping any. Standard pattern is
  `asyncio.Queue.put_nowait` from the watcher thread + an async
  consumer task. Confirm the consumer's backpressure semantics
  (drop-oldest vs block).
- **O2 — `WALmode default in `db.py:open_index_database`.** Verify
  + enable if missing.
- **O3 — Cross-platform CI matrix.** Today CI runs `ubuntu-latest`.
  Should `--watch` testing run on macOS too? If yes: budget for
  one extra macOS job.
- **O4 — `_run_watch_loop` placement.** Module-level helper in
  `__main__.py` (matches `_run_indexing` / `_run_search` style) or
  a method on a new `ServeOrchestrator` class? Lean toward the
  former for consistency with the existing CLI shape.
- **O5 — Logging cadence.** Each reindex-trigger logs the triggering
  file path. Too chatty? Cap at e.g. 5 files per log line + a
  count for the rest.
- **O6 — Should `--watch` imply `--verbose`-style logging by
  default?** Probably no — users can opt in with `-v`.

## 9. Next step

Brainstorm reviewer signs off on this spec → invoke
`superpowers:writing-plans` to generate the bite-sized TDD task plan
→ optionally dispatch via `superpowers:subagent-driven-development`.
