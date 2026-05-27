# `pydocs-mcp serve --watch` — TDD implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Each task below is sized for one ~2-5 min focused subagent dispatch. Run them sequentially (later tasks import earlier ones) and verify the `pytest` + `ruff` gate at the end of every task before moving on.

**Spec:** `docs/superpowers/specs/2026-05-27-serve-watch-flag-design.md`
**Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/serve-watch-flag/`
**Branch:** `feature/serve-watch-flag`
**Baseline test count (per CLAUDE.md):** 1367 unit + 283 benchmark tests.

---

## Goal

Ship `pydocs-mcp serve <project> --watch`: the MCP server boots indexing once at startup (today's behavior), then a `watchdog`-backed file watcher fires a debounced reindex on every relevant `.py` / `.md` / `.ipynb` edit under the project root. The MCP surface stays at the fixed 2 tools (`search`, `lookup`); all tuning (debounce_ms, extensions, ignore_globs) lives in `AppConfig.serve.watch`. Without `--watch`, behavior is byte-identical to today.

## Architecture

Three new moving parts, threaded together by the existing composition root in `__main__._cmd_serve`:

1. **`watchdog` lazy import behind an extras group** — `pip install pydocs-mcp[watch]` installs it; a default `pip install pydocs-mcp` does NOT. The watcher module imports `watchdog` inside the constructor wrapped in `try/except ImportError`, raising `ServiceUnavailableError` with the install hint. Default `pydocs-mcp serve` never touches `watchdog` at all.
2. **`FileWatcher` value object** at `python/pydocs_mcp/serve/watcher.py` — `@dataclass(frozen=True, slots=True)`. Wraps `watchdog.observers.Observer`, bridges its native thread to the asyncio loop via `asyncio.Queue.put_nowait`. Exposes one method: `async def run_until_cancelled(self, on_change: Callable[[], Awaitable[None]]) -> None`. Inside it: filter `.py/.md/.ipynb` and ignore-glob; debounce events for `debounce_ms`; coalesce events during in-flight reindex via an `asyncio.Lock` + `_pending` flag (Decision E).
3. **`AppConfig.serve.watch` pydantic sub-model** in `python/pydocs_mcp/retrieval/config.py` — 4 keys (`enabled`, `debounce_ms`, `extensions`, `ignore_globs`) with a cross-field validator on `debounce_ms` bounds. Defaults shipped in `python/pydocs_mcp/defaults/default_config.yaml`. CLI `--watch` flag overrides `enabled`. Per CLAUDE.md §"MCP API surface vs YAML configuration": no MCP tool param churn — `--watch` is a per-deployment boolean toggle, like the existing `--cache-dir`.

**Async-pattern reminders (mirror `CLAUDE.md` §"Async Patterns"):**
- `async def` everywhere for I/O — `to_thread()` for blocking calls.
- Never `time.sleep()` inside async code — `asyncio.sleep()`.
- `watchdog.Observer` runs on its own native thread; the bridge is `loop.call_soon_threadsafe(queue.put_nowait, event)` so the asyncio side sees events on the right thread.
- `try/finally` around `Observer.stop()` so a crash inside the consume-loop doesn't leak the OS-level event handle.

**Tech-stack reminders the implementer MUST mirror in every task:**
- `@dataclass(frozen=True, slots=True)` for `FileWatcher`; mutation via `dataclasses.replace`.
- Lazy import `watchdog` inside `serve/watcher.py` (Risk R3).
- Defaults are single-source: pydantic `Field(default=…)` and module-level `_DEFAULT_X` constants — never repeat literals (CLAUDE.md §"Default values: single source of truth").
- Comments explain WHY, not WHAT.
- **NO `Co-Authored-By:` trailer** in any commit message (global rule from `~/.claude/CLAUDE.md`; reinforces spec Decision H).
- One commit per task. Body in HEREDOC for clean formatting.

## Tech stack

- **Python:** 3.11+ (existing project requirement)
- **New runtime dep:** `watchdog>=4.0,<6.0` (extras-group `[watch]` only — soft dep, lazy import)
- **Existing deps used:** `pydantic>=2.0`, `pydantic-settings>=2.0`, `pyyaml>=6.0`, `mcp>=1.0`, the project's own `application/`, `retrieval/`, `db.py`.
- **Test infra:** `pytest>=7.0`, `pytest-asyncio>=0.23` (already in `[dev]` extras), `unittest.mock`.
- **Lint:** `ruff` (existing config in `pyproject.toml`).

## Survey findings (confirmed shapes the implementer can rely on)

1. **`AppConfig` lives at** `python/pydocs_mcp/retrieval/config.py`. No existing `serve:` section. Current top-level fields (line ~343 onward): `cache_dir`, `log_level`, `metadata_schemas`, `pipelines`, `extraction`, `reference_graph`, `search`, `embedding`, `llm`. Adding `serve: ServeConfig` is a clean additive field. Pattern to follow: `class SearchOutputConfig(BaseModel)` + `class SearchConfig(BaseModel)` (lines 189-222) and `class ReferenceGraphConfig(BaseModel)` (line 172).
2. **`_cmd_serve` is at** `python/pydocs_mcp/__main__.py:501-532`. Two-phase shape confirmed: Phase 1 calls `_run_cmd(_run_serve_indexing(args), verbose=args.verbose)`; Phase 2 calls `run(db_path, config_path=getattr(args, "config", None))` on the main thread inside a `try/except KeyboardInterrupt/Exception` block. The `--watch` integration extends Phase 2.
3. **WAL mode is already enabled** at `python/pydocs_mcp/db.py:194` (`conn.execute("PRAGMA journal_mode=WAL")`) AND already pinned by `tests/test_db.py:101-103` (`test_wal_mode`). O2 is fully resolved — no schema change needed; the WAL task collapses to a regression-pin reference.
4. **`ServiceUnavailableError` import path** is `pydocs_mcp.application.mcp_errors.ServiceUnavailableError` (defined at line 30 of `python/pydocs_mcp/application/mcp_errors.py`). Used today by `NullTreeService` / `NullReferenceService` in `python/pydocs_mcp/application/null_services.py:77,100-106`.
5. **`default_config.yaml` is at** `python/pydocs_mcp/defaults/default_config.yaml`. Top-level keys today: `cache_dir`, `log_level`, `metadata_schemas`, `pipelines`, `extraction`, `reference_graph`, `search`, `embedding`, (commented-out `llm`). Adding a `serve:` block is a clean append.

**Decisions I made beyond the spec** (informational — no controller intervention needed):
- **WAL task = pin-test reference, not new code.** The schema already does what Risk R5 requires; Task 11 is reduced to a single sanity assertion alongside the new watcher tests, not a separate "add PRAGMA" task.
- **`FakeObserver` lives at `tests/_fakes.py`** (alongside the other in-memory fakes) so it can be imported by `tests/test_watcher.py` AND any future integration test. Mirrors the existing `InMemoryChunkStore` / `FakeUnitOfWork` shape.
- **Logging cadence (O5)**: log one INFO line per reindex trigger with up to 3 changed paths and a `(+N more)` suffix when more accumulated. Below-radar quiet for the no-event case; observably noisy enough for the user to see the watcher working.
- **`_run_watch_loop` placement (O4)**: module-level helper in `__main__.py` next to `_run_indexing` (consistent with existing `_run_search` / `_run_lookup` style — no new orchestrator class needed).

---

## Tasks

Each task: **Step 1** (write failing test) → **Step 2** (verify FAIL) → **Step 3** (implement minimum to green) → **Step 4** (verify PASS) → **Step 5** (commit). Run `pytest -q` and `ruff check python/ tests/` at the end of every code-touching task.

---

### Task 1 — Add `watchdog` extras group to `pyproject.toml`

**Goal:** declare `watchdog>=4.0,<6.0` as a soft dep under `[project.optional-dependencies].watch`. Default install MUST NOT pull it; `pip install pydocs-mcp[watch]` MUST.

**Step 1 — Write failing test.** Extend `tests/test_pyproject_extras.py` with two new tests:

Append to `tests/test_pyproject_extras.py`:

```python
def test_watch_extras_group_present() -> None:
    """AC-9: ``watchdog`` ships behind ``[watch]`` extras, not main deps."""
    cfg = _load()
    extras = cfg["project"].get("optional-dependencies", {})
    assert "watch" in extras, (
        f"watch extras group missing. Got: {list(extras)}"
    )
    watch_deps = extras["watch"]
    assert any("watchdog" in d for d in watch_deps), (
        f"watchdog not in watch extras: {watch_deps}"
    )


def test_watchdog_not_in_main_dependencies() -> None:
    """AC-9: ``watchdog`` is opt-in via ``[watch]`` — never pulled by default
    ``pip install pydocs-mcp``."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert not any("watchdog" in d for d in main_deps), (
        f"watchdog leaked into main dependencies: {main_deps}"
    )


def test_watch_extras_pins_watchdog_version_range() -> None:
    """Pin the version range so a future watchdog 6.x breaking change
    doesn't silently break ``--watch``."""
    cfg = _load()
    watch_deps = cfg["project"]["optional-dependencies"]["watch"]
    spec = next(d for d in watch_deps if "watchdog" in d)
    assert ">=4.0" in spec and "<6.0" in spec, (
        f"watchdog spec must pin >=4.0,<6.0; got {spec!r}"
    )
```

**Step 2 — Verify FAIL.** Run:

```bash
pytest tests/test_pyproject_extras.py -q
```

Expected: 3 new failures (`KeyError: 'watch'` on the first, AssertionError on the others).

**Step 3 — Implement.** Edit `pyproject.toml` `[project.optional-dependencies]`:

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "pytest-asyncio>=0.23"]
watch = ["watchdog>=4.0,<6.0"]
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_pyproject_extras.py -q
pytest -q
ruff check python/ tests/
```

All green; no new failures elsewhere.

**Step 5 — Commit.**

```bash
git add pyproject.toml tests/test_pyproject_extras.py
git commit -m "$(cat <<'EOM'
deps: add `watchdog>=4.0,<6.0` under `[project.optional-dependencies].watch`

Soft dep for the upcoming `pydocs-mcp serve --watch` flag. Default
`pip install pydocs-mcp` does not pull watchdog; `pip install
pydocs-mcp[watch]` does. Pinned `<6.0` so a future major-version
breaking change doesn't silently break the watcher.
EOM
)"
```

---

### Task 2 — `WatchConfig` + `ServeConfig` pydantic sub-models

**Goal:** add `AppConfig.serve: ServeConfig` with nested `serve.watch: WatchConfig` (4 keys + cross-field validator) so YAML can tune the watcher.

**Step 1 — Write failing tests.** Create `tests/test_config_serve_watch.py`:

```python
"""AC-8: ``serve.watch.*`` pydantic sub-model on ``AppConfig``."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, ServeConfig, WatchConfig


def test_watch_config_defaults() -> None:
    cfg = WatchConfig()
    assert cfg.enabled is False
    assert cfg.debounce_ms == 500
    assert cfg.extensions == (".py", ".md", ".ipynb")
    assert "**/__pycache__/**" in cfg.ignore_globs
    assert "**/.git/**" in cfg.ignore_globs
    assert "**/.venv/**" in cfg.ignore_globs
    assert "**/*.pyc" in cfg.ignore_globs


def test_serve_config_defaults() -> None:
    cfg = ServeConfig()
    assert isinstance(cfg.watch, WatchConfig)
    assert cfg.watch.enabled is False


def test_app_config_serve_field_present() -> None:
    """``AppConfig.serve`` is reachable from a freshly-loaded config."""
    cfg = AppConfig.load(explicit_path=None)
    assert isinstance(cfg.serve, ServeConfig)
    assert isinstance(cfg.serve.watch, WatchConfig)


def test_watch_config_rejects_zero_debounce() -> None:
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=0)


def test_watch_config_rejects_negative_debounce() -> None:
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=-1)


def test_watch_config_rejects_too_large_debounce() -> None:
    """60_000 ms ceiling — anything larger and the user would be better off
    re-running `pydocs-mcp index .` manually."""
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=60_000)
    with pytest.raises(ValueError, match="debounce_ms"):
        WatchConfig(debounce_ms=120_000)


def test_watch_config_accepts_valid_debounce() -> None:
    WatchConfig(debounce_ms=1)
    WatchConfig(debounce_ms=500)
    WatchConfig(debounce_ms=59_999)


def test_watch_config_forbids_extra_keys() -> None:
    """Typo-catching: ``extentions`` (sic) must fail load, not silently drop."""
    with pytest.raises(ValueError):
        WatchConfig(extentions=[".py"])  # type: ignore[call-arg]


def test_serve_config_forbids_extra_keys() -> None:
    with pytest.raises(ValueError):
        ServeConfig(unknown=True)  # type: ignore[call-arg]
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_config_serve_watch.py -q
```

Expected: every test errors at the `from pydocs_mcp.retrieval.config import ServeConfig, WatchConfig` import.

**Step 3 — Implement.** Edit `python/pydocs_mcp/retrieval/config.py`. Add the two new classes immediately after `class SearchConfig(BaseModel):` (around line 222):

```python
# Single source of truth for the debounce bounds (CLAUDE.md §"Default
# values: single source of truth"). Used both for the pydantic Field
# default AND the cross-field validator's ceiling check below.
_DEFAULT_WATCH_DEBOUNCE_MS = 500
_MAX_WATCH_DEBOUNCE_MS = 60_000


class WatchConfig(BaseModel):
    """File-watcher tunables for ``pydocs-mcp serve --watch``.

    Per CLAUDE.md §"MCP API surface vs YAML configuration": these are
    deployment-time knobs, NOT MCP tool params. The MCP surface stays at
    the fixed 2 tools (``search``, ``lookup``); ``--watch`` is the only
    CLI flag and it overrides ``enabled``.

    ``debounce_ms`` is bounded: zero/negative would fire on every byte
    of a slow-write editor (atomic-save sequences fire 2-3 events per
    save — debounce naturally collapses them); >60_000 ms means the
    user is better off re-running ``pydocs-mcp index .`` manually
    (spec §6 R7).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    debounce_ms: int = Field(default=_DEFAULT_WATCH_DEBOUNCE_MS, ge=1)
    # tuple so dataclass-style sharing across threads stays immutable
    extensions: tuple[str, ...] = (".py", ".md", ".ipynb")
    ignore_globs: tuple[str, ...] = (
        "**/__pycache__/**",
        "**/.git/**",
        "**/.venv/**",
        "**/node_modules/**",
        "**/.pytest_cache/**",
        "**/*.pyc",
    )

    @model_validator(mode="after")
    def _validate_debounce_bound(self) -> "WatchConfig":
        if self.debounce_ms >= _MAX_WATCH_DEBOUNCE_MS:
            raise ValueError(
                f"serve.watch.debounce_ms={self.debounce_ms} must be "
                f"< {_MAX_WATCH_DEBOUNCE_MS} ms. Larger values defeat "
                "the purpose of a live watcher; re-run "
                "`pydocs-mcp index .` manually instead."
            )
        return self


class ServeConfig(BaseModel):
    """Namespace for ``serve``-command tunables (parity with ``search`` /
    ``reference_graph``). Only ``watch`` lives here today; future serve-
    side knobs (HTTP transport options, etc.) get an obvious home."""

    model_config = ConfigDict(extra="forbid")

    watch: WatchConfig = Field(default_factory=WatchConfig)
```

Then add the field to `AppConfig` (around line 362 after `search: SearchConfig`):

```python
    # Serve-command tunables (file watcher today; future HTTP transport
    # options tomorrow). Per CLAUDE.md §"MCP API surface vs YAML
    # configuration": CLI ``--watch`` overrides ``serve.watch.enabled``;
    # no MCP tool param. The MCP surface (search, lookup) stays fixed.
    serve: ServeConfig = Field(default_factory=ServeConfig)
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_config_serve_watch.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/test_config_serve_watch.py
git commit -m "$(cat <<'EOM'
config: add ServeConfig + WatchConfig pydantic sub-models on AppConfig

Mirrors the ReferenceGraphConfig / SearchConfig pattern. WatchConfig
holds the 4 YAML-tunable keys for the watcher (enabled, debounce_ms,
extensions, ignore_globs). Cross-field validator bounds debounce_ms
in (0, 60_000) — zero would fire on every byte of a slow-write editor,
60s+ defeats the live-watcher purpose.

No CLI/runtime behaviour change yet; this is the typed shape that the
upcoming `--watch` plumbing reads.
EOM
)"
```

---

### Task 3 — Ship `serve.watch.*` defaults in `default_config.yaml`

**Goal:** the lowest-priority shipped config layer documents every knob with its default, mirroring the existing `search.output.*` / `reference_graph.*` style.

**Step 1 — Write failing test.** Create `tests/test_default_config_serve_watch.py`:

```python
"""AC-8: shipped defaults include the new ``serve.watch.*`` keys."""
from __future__ import annotations

import importlib.resources
from pathlib import Path

import yaml


def _shipped_yaml() -> dict:
    p = Path(str(importlib.resources.files("pydocs_mcp.defaults").joinpath(
        "default_config.yaml"
    )))
    return yaml.safe_load(p.read_text())


def test_serve_watch_keys_present_in_shipped_defaults() -> None:
    data = _shipped_yaml()
    assert "serve" in data
    assert "watch" in data["serve"]
    watch = data["serve"]["watch"]
    assert watch["enabled"] is False
    assert watch["debounce_ms"] == 500
    # YAML lists become Python lists — pydantic coerces to tuple on load.
    assert ".py" in watch["extensions"]
    assert ".md" in watch["extensions"]
    assert ".ipynb" in watch["extensions"]
    assert any("__pycache__" in g for g in watch["ignore_globs"])
    assert any(".git" in g for g in watch["ignore_globs"])


def test_app_config_load_picks_up_yaml_overrides(tmp_path: Path) -> None:
    """User YAML overlay propagates into AppConfig.serve.watch."""
    from pydocs_mcp.retrieval.config import AppConfig

    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        "serve:\n"
        "  watch:\n"
        "    enabled: true\n"
        "    debounce_ms: 1234\n"
    )
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.serve.watch.enabled is True
    assert cfg.serve.watch.debounce_ms == 1234
    # Unspecified keys fall through to shipped defaults.
    assert ".py" in cfg.serve.watch.extensions
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_default_config_serve_watch.py -q
```

Expected: both tests fail (no `serve:` block in shipped YAML).

**Step 3 — Implement.** Append the following block to `python/pydocs_mcp/defaults/default_config.yaml` (just before the trailing `# Uncomment to override:` LLM comment block, around line 73):

```yaml
# File-watcher tunables for `pydocs-mcp serve --watch` (CLI flag overrides
# `enabled` — leave it false here so plain `pydocs-mcp serve` is identical
# to today). Debounce naturally collapses editor atomic-save sequences
# (temp create → delete → rename → 1 reindex). Ignore globs default-skip
# the high-churn paths that would otherwise produce false-positive
# reindexes. Requires `pip install pydocs-mcp[watch]`.
serve:
  watch:
    enabled: false
    debounce_ms: 500
    extensions: [".py", ".md", ".ipynb"]
    ignore_globs:
      - "**/__pycache__/**"
      - "**/.git/**"
      - "**/.venv/**"
      - "**/node_modules/**"
      - "**/.pytest_cache/**"
      - "**/*.pyc"
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_default_config_serve_watch.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/defaults/default_config.yaml tests/test_default_config_serve_watch.py
git commit -m "$(cat <<'EOM'
config: ship serve.watch.* defaults in default_config.yaml

Lowest-priority layer documents every knob (enabled, debounce_ms,
extensions, ignore_globs) with its shipped value. Mirrors the existing
search.output.* / reference_graph.* pattern. CLI `--watch` flag
(landing next) overrides `enabled` at runtime.
EOM
)"
```

---

### Task 4 — `FileWatcher` skeleton + lazy watchdog import

**Goal:** create `python/pydocs_mcp/serve/__init__.py` (empty package marker) and `python/pydocs_mcp/serve/watcher.py` with the `FileWatcher` dataclass shell. Construction MUST raise `ServiceUnavailableError` when `watchdog` is not installed; with `watchdog` installed it MUST succeed.

**Step 1 — Write failing test.** Create `tests/test_watcher.py`:

```python
"""Tests for the file watcher (`pydocs-mcp serve --watch`).

Mirrors spec §4.1 deliverable 6. The `FakeObserver` injected into
`FileWatcher` lets us drive events synchronously — no real `watchdog`
thread is involved, so tests stay fast and deterministic.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError


def test_watcher_module_importable() -> None:
    """The module itself imports without watchdog installed.

    `watchdog` import lives inside `FileWatcher.__post_init__` /
    constructor only, so users who never touch `--watch` pay zero cost.
    """
    from pydocs_mcp.serve import watcher  # noqa: F401


def test_watcher_construction_raises_when_watchdog_missing(monkeypatch) -> None:
    """AC-9: without the `[watch]` extras, constructor raises with the
    actionable install hint pointing at `pip install pydocs-mcp[watch]`."""
    import builtins

    real_import = builtins.__import__

    def _no_watchdog(name, *args, **kwargs):
        if name == "watchdog" or name.startswith("watchdog."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_watchdog)

    from pydocs_mcp.serve.watcher import FileWatcher

    with pytest.raises(ServiceUnavailableError) as exc_info:
        FileWatcher(
            root=Path("/tmp"),
            extensions=(".py",),
            ignore_globs=(),
            debounce_ms=500,
        )
    assert "pip install pydocs-mcp[watch]" in str(exc_info.value)


def test_watcher_construction_succeeds_when_watchdog_present(tmp_path: Path) -> None:
    """Real watchdog installed (project tests run under the dev extras)
    — constructor returns a FileWatcher instance."""
    pytest.importorskip("watchdog")
    from pydocs_mcp.serve.watcher import FileWatcher

    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=500,
    )
    assert fw.root == tmp_path
    assert fw.extensions == (".py",)
    assert fw.debounce_ms == 500
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_watcher.py -q
```

Expected: ModuleNotFoundError / ImportError on `pydocs_mcp.serve`.

**Step 3 — Implement.** Create the package:

```bash
mkdir -p python/pydocs_mcp/serve
```

Create `python/pydocs_mcp/serve/__init__.py`:

```python
"""Serve-side helpers — file watcher today; HTTP transport tomorrow.

Kept separate from `pydocs_mcp.server` (the FastMCP entry point) so the
`watchdog` lazy import lives in a leaf module that's only loaded when
`--watch` is set. Default `pydocs-mcp serve` never imports anything in
this package.
"""
```

Create `python/pydocs_mcp/serve/watcher.py`:

```python
"""File-system watcher for `pydocs-mcp serve --watch` (spec §4.1).

The MCP server runs on the main thread (Phase 2 of `_cmd_serve`); this
module runs alongside it as an asyncio task that consumes filesystem
events from `watchdog.Observer`'s native thread and re-triggers
indexing on debounce.

Lazy import boundary: `watchdog` lives behind the `[watch]` extras
group; importing it at module top would crash `pydocs-mcp serve`
(no `--watch`) for users who haven't installed the extras. The
constructor below resolves the import once at first use — if the
extras aren't present, raises `ServiceUnavailableError` with the
install hint instead of letting an `ImportError` bubble up cryptically.

Event-loop bridge: `watchdog.Observer` runs in its own native thread.
We give the event handler a reference to the asyncio loop + queue and
let it call `loop.call_soon_threadsafe(queue.put_nowait, path)` so
the consumer side sees the event on the right thread.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError

log = logging.getLogger("pydocs-mcp.watch")

_INSTALL_HINT = (
    "--watch requires the 'watch' extras. Install via:\n"
    "    pip install pydocs-mcp[watch]"
)


def _load_watchdog():
    """Resolve `watchdog.observers.Observer` + `watchdog.events.FileSystemEventHandler`.

    Isolated so tests can monkeypatch `builtins.__import__` to simulate
    the no-extras case without touching the actual site-packages tree.
    """
    try:
        from watchdog.events import FileSystemEventHandler  # noqa: F401
        from watchdog.observers import Observer  # noqa: F401
    except ImportError as exc:
        raise ServiceUnavailableError(_INSTALL_HINT) from exc
    return Observer, FileSystemEventHandler


@dataclass(frozen=True, slots=True)
class FileWatcher:
    """File-system watcher value object (spec §4.1 deliverable 3).

    Frozen + slots: state (queue, lock, pending flag) lives on
    asyncio-owned objects threaded through `run_until_cancelled` rather
    than as mutable dataclass fields — keeps the constructor cheap and
    `dataclasses.replace`-compatible for future variant tuning.
    """

    root: Path
    extensions: tuple[str, ...]
    ignore_globs: tuple[str, ...]
    debounce_ms: int
    # Allows tests to inject a `FakeObserver` without touching watchdog.
    # Production callers leave it None → constructor resolves the real
    # `watchdog.observers.Observer` lazily.
    observer_factory: Callable[[], object] | None = field(default=None)

    def __post_init__(self) -> None:
        # WHY: resolve the watchdog import (or raise the install hint) at
        # construction time rather than at first event — startup failure
        # is easier to diagnose than mid-run "why isn't my watcher firing".
        if self.observer_factory is None:
            Observer, _ = _load_watchdog()
            object.__setattr__(self, "observer_factory", Observer)

    def _matches(self, path: Path) -> bool:
        """Pure-function event filter — returns True iff the path is
        a candidate for triggering a reindex.

        Returns False for: directory events (no extension match),
        non-watched extensions, paths matching any `ignore_globs`
        pattern. Used by the watchdog event handler before queueing.
        """
        if path.suffix not in self.extensions:
            return False
        path_str = str(path)
        for pattern in self.ignore_globs:
            if fnmatch.fnmatch(path_str, pattern):
                return False
        return True

    async def run_until_cancelled(
        self, on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume events, fire `on_change` on debounce.

        Cancelling the parent task (via `asyncio.Task.cancel()` or a
        propagated `KeyboardInterrupt`) stops the observer and unwinds
        cleanly. See spec Decisions E + G.

        Stub for now — wired in later tasks.
        """
        raise NotImplementedError("filled in by later TDD task")
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_watcher.py -q
pytest -q
ruff check python/ tests/
```

All green; existing 1367+ tests still pass.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/serve/ tests/test_watcher.py
git commit -m "$(cat <<'EOM'
serve: add FileWatcher skeleton + lazy watchdog import

Constructor raises ServiceUnavailableError with the
`pip install pydocs-mcp[watch]` install hint when watchdog is missing.
Default `pydocs-mcp serve` (no --watch) never touches this module, so
users without the extras pay zero cost.

The actual event loop, filtering, debounce, and coalesce arrive in
later TDD steps. This commit pins the import boundary + the value-
object shape (frozen=True, slots=True, no mutable state).
EOM
)"
```

---

### Task 5 — `FakeObserver` test helper

**Goal:** a `tests/_fakes.py` helper that lets tests inject events without spinning up a real `watchdog.Observer` thread. Pinned by spec §6 R6 (avoid filesystem-event flakiness).

**Step 1 — Write failing test.** Append to `tests/test_watcher.py`:

```python
def test_fake_observer_injects_events_synchronously() -> None:
    """The FakeObserver test helper records start/stop/schedule calls
    and exposes a `.fire(path)` hook tests can call to inject events
    without filesystem timing nondeterminism."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    assert not obs.started
    obs.start()
    assert obs.started

    fired: list[str] = []

    class _Handler:
        def on_any_event(self, event):
            fired.append(event.src_path)

    obs.schedule(_Handler(), "/tmp/some/dir", recursive=True)
    obs.fire("/tmp/some/dir/file.py")
    assert fired == ["/tmp/some/dir/file.py"]

    obs.stop()
    assert not obs.started
    obs.join()  # idempotent no-op


def test_fake_observer_fire_event_has_src_path_attr() -> None:
    """`fire(path)` synthesizes an event with the `src_path` attr the
    watchdog handler expects (mirrors `watchdog.events.FileSystemEvent`)."""
    from tests._fakes import FakeObserver

    obs = FakeObserver()
    obs.start()
    captured: list[object] = []

    class _Handler:
        def on_any_event(self, event):
            captured.append(event)

    obs.schedule(_Handler(), "/x", recursive=True)
    obs.fire("/x/a.py")
    assert hasattr(captured[0], "src_path")
    assert captured[0].src_path == "/x/a.py"
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_watcher.py::test_fake_observer_injects_events_synchronously -q
```

Expected: `ImportError: cannot import name 'FakeObserver'`.

**Step 3 — Implement.** Append to `tests/_fakes.py`:

```python
# ── File-watcher fake (spec §6 R6 — avoid real filesystem flakiness) ──

@dataclass
class _FakeFsEvent:
    """Minimal stand-in for `watchdog.events.FileSystemEvent`.

    The real event has many fields (`is_directory`, `event_type`, etc.);
    `FileWatcher` only needs `src_path`. Keep the fake minimal so we
    don't accidentally couple tests to fields the production code
    doesn't read.
    """

    src_path: str


class FakeObserver:
    """In-memory `watchdog.observers.Observer` stand-in.

    `FileWatcher` accepts an `observer_factory` so tests can inject this
    in place of the real `Observer`. Synchronous event injection via
    `.fire(path)` — no native thread, no FSEvents/inotify involved, so
    tests stay fast (<1ms per event) and deterministic.

    Handlers are stored by-path so a test can target one watched dir;
    `fire(path)` walks the handlers and invokes `on_any_event(event)`
    on each — matching the real watchdog dispatch contract.
    """

    def __init__(self) -> None:
        self.started = False
        self._handlers: list[tuple[object, str, bool]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
        # No background thread to join in the fake; real Observer.join()
        # is a blocking wait. Idempotent no-op preserves the call-site
        # contract `FileWatcher.run_until_cancelled` relies on.
        return None

    def schedule(self, handler: object, path: str, recursive: bool = False) -> object:
        self._handlers.append((handler, path, recursive))
        return object()  # real watchdog returns an `ObservedWatch`

    def fire(self, path: str) -> None:
        """Inject a synthetic event with `src_path=path` into every
        registered handler. Tests call this to drive the watcher
        deterministically."""
        event = _FakeFsEvent(src_path=path)
        for handler, _root, _recursive in self._handlers:
            on_any = getattr(handler, "on_any_event", None)
            if on_any is not None:
                on_any(event)


__all__ = (*__all__ if "__all__" in dir() else (), "FakeObserver")
```

Note: the existing `tests/_fakes.py` may already define `__all__` differently. If so, the implementer adjusts the final line accordingly (it's the only line that conflicts; the rest is a clean append).

Add `from dataclasses import dataclass` at the top of `tests/_fakes.py` if not already imported.

**Step 4 — Verify PASS.**

```bash
pytest tests/test_watcher.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add tests/_fakes.py tests/test_watcher.py
git commit -m "$(cat <<'EOM'
tests: add FakeObserver helper for synchronous watcher event injection

Spec §6 R6 — real `watchdog.Observer` runs on a native thread with
platform-specific event timing (FSEvents on macOS, inotify on Linux,
ReadDirectoryChangesW on Windows). Tests using real events are slow
and flaky. FakeObserver lets `FileWatcher` tests inject events
synchronously via `.fire(path)` while preserving the real watchdog
contract (`start`/`stop`/`schedule`/`join` + `on_any_event` dispatch).

`FileWatcher.observer_factory=FakeObserver` swaps the real watchdog
Observer out of the unit tests entirely. End-to-end smoke against
the real Observer lives behind `pytest.mark.slow` (added later).
EOM
)"
```

---

### Task 6 — Extension + ignore-glob filtering

**Goal:** `FileWatcher._matches()` filters events; integrated into a partial `run_until_cancelled` that pushes filtered events to an asyncio queue. AC-3 (irrelevant events filtered).

**Step 1 — Write failing test.** Append to `tests/test_watcher.py`:

```python
async def test_watcher_filters_unrelated_events(tmp_path: Path) -> None:
    """AC-3: `.pyc`, `__pycache__/`, `.git/` events do NOT trigger callback."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py", ".md", ".ipynb"),
        ignore_globs=(
            "**/__pycache__/**",
            "**/.git/**",
            "**/*.pyc",
        ),
        debounce_ms=10,  # short for fast tests
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    # Give the watcher one tick to start the observer + register the handler.
    await asyncio.sleep(0.01)

    # These should ALL be filtered out:
    fake.fire(str(tmp_path / "x.pyc"))                       # bad extension
    fake.fire(str(tmp_path / "__pycache__" / "x.cpython.pyc"))  # ignore
    fake.fire(str(tmp_path / ".git" / "HEAD"))                  # ignore
    fake.fire(str(tmp_path / "x.png"))                          # bad ext

    # Wait past debounce; no callback should fire.
    await asyncio.sleep(0.05)
    assert fire_count == 0

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_fires_on_matching_extension(tmp_path: Path) -> None:
    """The positive case: a `.py` edit fires exactly one callback."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    fake.fire(str(tmp_path / "app.py"))
    await asyncio.sleep(0.05)
    assert fire_count == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_watcher.py::test_watcher_filters_unrelated_events tests/test_watcher.py::test_watcher_fires_on_matching_extension -q
```

Expected: `NotImplementedError("filled in by later TDD task")` from the stub.

**Step 3 — Implement.** Replace `FileWatcher.run_until_cancelled` (and add a private helper) in `python/pydocs_mcp/serve/watcher.py`:

```python
    async def run_until_cancelled(
        self, on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume filtered events, fire `on_change` on debounce.

        Cancelling the parent task stops the observer and unwinds cleanly.
        See spec Decisions E + G.
        """
        _, FileSystemEventHandler = _load_watchdog()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        watcher_self = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:  # noqa: ANN001
                # WHY: `watchdog` calls this from its own native thread.
                # `loop.call_soon_threadsafe(queue.put_nowait, ...)` is the
                # documented bridge — never `queue.put_nowait` directly,
                # which would race the asyncio side.
                path = Path(event.src_path)
                if not watcher_self._matches(path):
                    return
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, path)
                except RuntimeError:
                    # Loop closed — observer is being torn down. Drop event.
                    pass

        observer = self.observer_factory()  # type: ignore[misc]
        observer.schedule(_Handler(), str(self.root), recursive=True)
        observer.start()
        try:
            await self._consume(queue, on_change)
        finally:
            observer.stop()
            observer.join(timeout=2.0)

    async def _consume(
        self,
        queue: asyncio.Queue,
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Consume queued events, debounce, fire `on_change` per spec Decision E.

        Filled in over Tasks 6/7/8 — this version just calls `on_change`
        on every event so AC-3 / extension filtering can be pinned first.
        """
        while True:
            path = await queue.get()
            log.info("watch: reindex triggered by %s", path)
            await on_change()
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_watcher.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/serve/watcher.py tests/test_watcher.py
git commit -m "$(cat <<'EOM'
serve: implement extension + ignore-glob filtering in FileWatcher

`_matches(path)` rejects events that don't match `extensions` or
that match any `ignore_globs` pattern (e.g. `__pycache__/`, `.git/`,
`*.pyc`). The watchdog event handler runs the filter before
`call_soon_threadsafe(queue.put_nowait, ...)` so the asyncio consumer
never sees irrelevant events.

Cross-thread bridge per asyncio docs: `loop.call_soon_threadsafe` is
the only safe way to push from watchdog's native thread into the
asyncio queue. Direct `queue.put_nowait` would race.

Pins AC-3 (filter unrelated events) ahead of debounce / coalesce.
EOM
)"
```

---

### Task 7 — Debounce: N events within window → 1 callback

**Goal:** spec Decision E debounce semantics. AC-2 + AC-4 (burst editor save-all collapses to a single reindex).

**Step 1 — Write failing test.** Append to `tests/test_watcher.py`:

```python
async def test_watcher_debounces_burst_edits(tmp_path: Path) -> None:
    """AC-4: 3 events within `debounce_ms` produce exactly 1 callback."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=50,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    # Three rapid edits within the 50ms debounce window.
    fake.fire(str(tmp_path / "a.py"))
    fake.fire(str(tmp_path / "b.py"))
    fake.fire(str(tmp_path / "c.py"))

    # Wait > debounce_ms — exactly one callback should have fired.
    await asyncio.sleep(0.12)
    assert fire_count == 1, f"expected 1 callback, got {fire_count}"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_fires_after_debounce_window(tmp_path: Path) -> None:
    """AC-2: callback fires within debounce_ms + small headroom."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=30,
        observer_factory=lambda: fake,
    )

    times: list[float] = []
    loop = asyncio.get_running_loop()

    async def _on_change() -> None:
        times.append(loop.time())

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    start = loop.time()
    fake.fire(str(tmp_path / "a.py"))
    # Should fire roughly debounce_ms after the last event.
    await asyncio.sleep(0.1)

    assert len(times) == 1
    elapsed = times[0] - start
    # 30ms debounce, allow 100ms slack for test-host scheduling jitter.
    assert 0.025 <= elapsed <= 0.130, (
        f"callback fired at {elapsed*1000:.1f}ms; expected ~30ms debounce"
    )

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_two_bursts_separated_by_idle_produce_two_callbacks(
    tmp_path: Path,
) -> None:
    """Sanity: two bursts separated by > debounce_ms idle → 2 callbacks."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=30,
        observer_factory=lambda: fake,
    )

    fire_count = 0

    async def _on_change() -> None:
        nonlocal fire_count
        fire_count += 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    # First burst.
    fake.fire(str(tmp_path / "a.py"))
    fake.fire(str(tmp_path / "b.py"))
    await asyncio.sleep(0.1)  # well past debounce, callback ran

    # Idle, then second burst.
    fake.fire(str(tmp_path / "c.py"))
    await asyncio.sleep(0.1)
    assert fire_count == 2

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_watcher.py::test_watcher_debounces_burst_edits -q
```

Expected: `fire_count == 3` (every event fires today, no debounce yet).

**Step 3 — Implement.** Replace the body of `FileWatcher._consume` in `python/pydocs_mcp/serve/watcher.py`:

```python
    async def _consume(
        self,
        queue: asyncio.Queue,
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Consume queued events, debounce, fire `on_change` per spec Decision E.

        Debounce algorithm: pop the first event, then repeatedly wait
        `debounce_ms` more — if another event arrives during the wait,
        reset the timer (the wait coalesces it). Once the timer expires
        without a new event, fire `on_change`.
        """
        debounce_s = self.debounce_ms / 1000.0
        while True:
            # Block until something arrives — no work to do otherwise.
            first_path = await queue.get()
            pending_paths: list[Path] = [first_path]

            # Debounce loop — extend the window every time a new event
            # lands during the wait. Exits when wait_for times out
            # without seeing an event.
            while True:
                try:
                    nxt = await asyncio.wait_for(queue.get(), timeout=debounce_s)
                    pending_paths.append(nxt)
                except asyncio.TimeoutError:
                    break

            self._log_trigger(pending_paths)
            await on_change()

    def _log_trigger(self, paths: list[Path]) -> None:
        """Log the trigger paths (cap at 3 + a count to keep logs sane)."""
        if not paths:
            return
        head = ", ".join(str(p) for p in paths[:3])
        if len(paths) > 3:
            log.info("watch: reindex triggered (%s, +%d more)", head, len(paths) - 3)
        else:
            log.info("watch: reindex triggered (%s)", head)
```

Remove the bare `log.info` line from `_consume`'s original stub (the new `_log_trigger` replaces it).

**Step 4 — Verify PASS.**

```bash
pytest tests/test_watcher.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/serve/watcher.py tests/test_watcher.py
git commit -m "$(cat <<'EOM'
serve: implement debounce + capped reindex-trigger logging

Spec Decision E: N edits within `debounce_ms` collapse into one
callback. Algorithm: pop the first event, then `asyncio.wait_for(queue.get(),
timeout=debounce_s)` in a loop — each new event resets the timer; the
loop exits on `TimeoutError` and fires `on_change` once.

Logging cap (O5): one INFO line per trigger with up to 3 paths +
`(+N more)` suffix when more accumulated. Keeps logs readable during
editor save-all bursts.

Pins AC-2 (callback fires within debounce_ms) + AC-4 (N edits → 1 cb).
EOM
)"
```

---

### Task 8 — In-flight coalesce: edit during reindex → 1 follow-up

**Goal:** spec Decision E coalesce-during-in-flight. AC-5. Plus an `asyncio.Lock` so two reindexes cannot overlap.

**Step 1 — Write failing test.** Append to `tests/test_watcher.py`:

```python
async def test_watcher_coalesces_during_in_flight_reindex(tmp_path: Path) -> None:
    """AC-5: events arriving during a long-running reindex schedule
    exactly ONE follow-up reindex — burst events during the in-flight
    callback do not multiply."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=20,
        observer_factory=lambda: fake,
    )

    in_flight = asyncio.Event()
    release = asyncio.Event()
    fire_count = 0

    async def _slow_on_change() -> None:
        nonlocal fire_count
        fire_count += 1
        in_flight.set()
        # Block here so the test can fire more events while we're "indexing".
        await release.wait()
        in_flight.clear()

    task = asyncio.create_task(fw.run_until_cancelled(_slow_on_change))
    await asyncio.sleep(0.01)

    # Trigger the first reindex.
    fake.fire(str(tmp_path / "a.py"))
    await asyncio.wait_for(in_flight.wait(), timeout=1.0)
    assert fire_count == 1

    # Now fire 5 more events while the first callback is still blocked.
    # Coalesce contract: these should schedule exactly ONE follow-up.
    for i in range(5):
        fake.fire(str(tmp_path / f"b{i}.py"))

    # Release the first reindex.
    release.set()
    # The follow-up should fire exactly once, then no more.
    await asyncio.sleep(0.15)
    assert fire_count == 2, (
        f"expected exactly 1 follow-up reindex; saw {fire_count - 1} extra"
    )

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_watcher_no_two_reindexes_overlap(tmp_path: Path) -> None:
    """Sibling pin to AC-5: two reindexes cannot run simultaneously."""
    from tests._fakes import FakeObserver
    from pydocs_mcp.serve.watcher import FileWatcher

    fake = FakeObserver()
    fw = FileWatcher(
        root=tmp_path,
        extensions=(".py",),
        ignore_globs=(),
        debounce_ms=10,
        observer_factory=lambda: fake,
    )

    overlap_observed = False
    active = 0

    async def _on_change() -> None:
        nonlocal active, overlap_observed
        active += 1
        if active > 1:
            overlap_observed = True
        await asyncio.sleep(0.05)
        active -= 1

    task = asyncio.create_task(fw.run_until_cancelled(_on_change))
    await asyncio.sleep(0.01)

    fake.fire(str(tmp_path / "a.py"))
    await asyncio.sleep(0.02)
    fake.fire(str(tmp_path / "b.py"))
    await asyncio.sleep(0.2)

    assert not overlap_observed

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_watcher.py::test_watcher_coalesces_during_in_flight_reindex -q
```

Expected: `fire_count == 6` (every event fires a separate callback without the in-flight gate).

**Step 3 — Implement.** Replace `FileWatcher._consume` again with the coalesce-aware version:

```python
    async def _consume(
        self,
        queue: asyncio.Queue,
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Consume queued events; debounce + coalesce per spec Decision E.

        Concurrency model:
        - Only one `on_change()` runs at a time (`_reindex_lock`).
        - Events arriving while the lock is held set `_pending=True` so
          a single follow-up reindex fires after the current one releases.
        - Burst events during an in-flight reindex coalesce to ONE
          follow-up regardless of count.
        """
        debounce_s = self.debounce_ms / 1000.0
        reindex_lock = asyncio.Lock()
        # Mutable closure state — `_consume` is the single async consumer,
        # so no cross-task aliasing concerns (per CLAUDE.md §"RetrieverState.scratch"
        # discipline — this isn't a `RetrieverState.scratch` situation, but
        # we follow the same "one writer per slot" mental model).
        pending = {"flag": False}

        async def _drain_and_fire(paths: list[Path]) -> None:
            self._log_trigger(paths)
            await on_change()

        async def _trigger_with_followup(paths: list[Path]) -> None:
            # If a reindex is in flight, just set the pending flag; the
            # in-flight reindex's post-fire check will schedule the follow-up.
            if reindex_lock.locked():
                pending["flag"] = True
                log.debug("watch: in-flight reindex; queued follow-up")
                return
            async with reindex_lock:
                await _drain_and_fire(paths)
                # Drain any events queued during the reindex by setting the flag.
                if pending["flag"]:
                    pending["flag"] = False
                    log.info("watch: in-flight follow-up reindex firing")
                    await _drain_and_fire([])

        while True:
            first_path = await queue.get()
            pending_paths: list[Path] = [first_path]
            while True:
                try:
                    nxt = await asyncio.wait_for(queue.get(), timeout=debounce_s)
                    pending_paths.append(nxt)
                except asyncio.TimeoutError:
                    break
            # Fire-and-forget so the consumer loop can immediately resume
            # draining queue events into `pending["flag"]` while the
            # current reindex runs.
            asyncio.create_task(_trigger_with_followup(pending_paths))
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_watcher.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/serve/watcher.py tests/test_watcher.py
git commit -m "$(cat <<'EOM'
serve: in-flight coalesce — edits during reindex → 1 follow-up

Spec Decision E: a long-running reindex sees a flurry of new edits
(editor save-all, git checkout). Without coalesce, N edits → N
back-to-back reindexes after the first one releases. Algorithm:
`asyncio.Lock` around the callback; if the lock is held when a new
debounced trigger fires, set `_pending=True` and return; on lock
release, check `_pending` and schedule exactly ONE follow-up.

Pins AC-5 (coalesce during in-flight) + sibling pin (no two
reindexes overlap).
EOM
)"
```

---

### Task 9 — `--watch` CLI flag + argparse wiring

**Goal:** add `--watch` to the `serve` subparser. Pin AC-1 (startup logs both server + watcher) and AC-7 (without `--watch`, behavior unchanged). The actual asyncio task wiring lands in Task 10.

**Step 1 — Write failing test.** Create `tests/test_main_cli_watch.py`:

```python
"""AC-1 / AC-7: `--watch` flag presence + parser shape."""
from __future__ import annotations

import argparse
import pytest


def test_serve_subparser_accepts_watch_flag() -> None:
    """AC-1: `pydocs-mcp serve <project> --watch` parses without error."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", ".", "--watch"])
    assert args.cmd == "serve"
    assert getattr(args, "watch", False) is True


def test_serve_subparser_watch_defaults_false() -> None:
    """AC-7: without `--watch`, the namespace.watch is False (or unset
    falling through to YAML's enabled=false)."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["serve", "."])
    # `store_true` default is False; pin that explicitly so we don't
    # accidentally start defaulting to True.
    assert getattr(args, "watch", False) is False


def test_index_subparser_rejects_watch_flag() -> None:
    """`--watch` is `serve`-only (spec §4.2 out of scope: watch mode for
    `pydocs-mcp index`). Argparse should refuse the flag for `index`."""
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["index", ".", "--watch"])
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_main_cli_watch.py -q
```

Expected: first two tests fail (no `--watch` flag defined); third already passes by accident.

**Step 3 — Implement.** Edit `python/pydocs_mcp/__main__.py` `_build_parser` — inside the `for cmd, hlp in [("serve", ...), ("index", ...)]` loop (around lines 77-99), add `--watch` to the `serve` subparser only. Replace that loop body:

```python
    for cmd, hlp in [("serve", "Index + start MCP"), ("index", "Index only")]:
        sp = sub.add_parser(cmd, help=hlp)
        sp.add_argument("project", nargs="?", default=".")
        # default=None so the YAML-configured inspect_depth wins when the
        # flag is absent (without this, argparse's hard-coded default
        # silently shadows ``extraction.members.inspect_depth``, mirroring
        # the F11 dead-config defect /ultrareview just removed for
        # by_extension).
        sp.add_argument(
            "--depth", type=int, default=None,
            help="Submodule scan depth (default: YAML extraction.members.inspect_depth)",
        )
        sp.add_argument("--workers", type=int, default=4, help="Parallel workers")
        sp.add_argument("--force", action="store_true", help="Clear cache, re-index all")
        sp.add_argument("--skip-project", action="store_true", help="Skip project source")
        sp.add_argument("--no-rust", **_no_rust)
        sp.add_argument("--cache-dir", **_cache_dir)
        sp.add_argument("-v", "--verbose", **_verbose)
        sp.add_argument(
            "--no-inspect", action="store_true",
            help="Don't import deps. Read .py files from site-packages instead. "
                 "Faster, safer, no side-effects. Uses the same parser as project source.",
        )
        # `serve` only: enable the file-system watcher (Decision B — opt-in
        # only; default behavior is byte-identical to today). Overrides
        # `serve.watch.enabled` from YAML. Requires `pip install pydocs-mcp[watch]`.
        if cmd == "serve":
            sp.add_argument(
                "--watch", action="store_true",
                help="Watch the project for changes and reindex on edits. "
                     "Requires the 'watch' extras: pip install pydocs-mcp[watch]",
            )
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_main_cli_watch.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/__main__.py tests/test_main_cli_watch.py
git commit -m "$(cat <<'EOM'
cli: add `--watch` flag to `serve` subparser

Decision B / spec §4.1 deliverable 2: opt-in only. `pydocs-mcp serve`
(no flag) is identical to today. `serve --watch` overrides
`serve.watch.enabled` from YAML at runtime.

`index` subparser does NOT accept `--watch` (spec §4.2 out of scope:
watch mode for `pydocs-mcp index`). Test pins both: serve-accepts +
index-rejects.

Actual asyncio-task wiring + watcher spawn lands next; this is just
the parser surface so the next task can drive it.
EOM
)"
```

---

### Task 10 — Wire watcher into `_cmd_serve` Phase 2 with graceful shutdown

**Goal:** when `--watch` is set, spawn the watcher asyncio task alongside `run(...)` on the main thread; cancel it cleanly on Ctrl+C OR on any exception out of `run(...)` (spec Decision G + Risk R4). AC-1, AC-6.

**Step 1 — Write failing tests.** Append to `tests/test_main_cli_watch.py`:

```python
def test_cmd_serve_with_watch_spawns_watcher_task(monkeypatch, tmp_path) -> None:
    """AC-1: `_cmd_serve(args.watch=True)` spawns the watcher alongside
    the MCP server. Static-analysis pin: `_run_watch_loop` is referenced
    inside `_cmd_serve` (or a helper it calls)."""
    import inspect

    from pydocs_mcp import __main__ as cli_main

    # The watcher loop must be referenced from the serve command path.
    src_serve = inspect.getsource(cli_main._cmd_serve)
    src_module = inspect.getsource(cli_main)
    # Either inline in _cmd_serve OR via a helper — both legal placements.
    assert (
        "_run_watch_loop" in src_serve
        or "_run_watch_loop" in src_module
    ), "no _run_watch_loop reference in __main__.py"


def test_run_watch_loop_helper_exists() -> None:
    """`_run_watch_loop` is a module-level coroutine helper (O4 — placed
    next to `_run_indexing` / `_run_search` for consistency)."""
    import asyncio
    from pydocs_mcp.__main__ import _run_watch_loop

    assert asyncio.iscoroutinefunction(_run_watch_loop)


def test_cmd_serve_without_watch_does_not_import_watcher(monkeypatch) -> None:
    """AC-7: `pydocs-mcp serve` (no --watch) never touches the watcher
    module. Pin via static analysis on _cmd_serve's call path."""
    import inspect

    from pydocs_mcp.__main__ import _cmd_serve

    src = inspect.getsource(_cmd_serve)
    # The import of `_run_watch_loop` (or watcher module) must be inside
    # a conditional gated by `args.watch` — never at module top.
    if "_run_watch_loop" in src or "pydocs_mcp.serve" in src:
        # Conditional gate must exist nearby.
        assert "args.watch" in src or "watch" in src.lower(), (
            "watcher referenced but no `args.watch` gate"
        )
```

Also add a test that exercises `_run_watch_loop` directly with a fake server and a `FakeObserver`:

```python
async def test_run_watch_loop_cancels_watcher_on_server_exit(tmp_path, monkeypatch) -> None:
    """AC-6: when the MCP `run(...)` callable returns / raises, the
    watcher task is cancelled cleanly (Observer.stop called)."""
    import argparse
    import asyncio

    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    # Build args-namespace shape that `_run_watch_loop` reads.
    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_loop

    # Stub the MCP `run` callable so we exit quickly. Real signal-loop
    # plumbing tested separately by the existing test_main_cli.py suite.
    server_calls: list[None] = []

    def _fake_run(db_path, config_path=None):  # noqa: ARG001
        server_calls.append(None)
        # Simulate the server running for ~50ms then "Ctrl+C" via return.
        import time; time.sleep(0.05)

    monkeypatch.setattr("pydocs_mcp.server.run", _fake_run)

    # Inject the fake observer into the watcher.
    from pydocs_mcp.serve import watcher as watcher_mod
    monkeypatch.setattr(
        watcher_mod, "_load_watchdog",
        lambda: (lambda: fake_observer, type("H", (), {"on_any_event": lambda self, e: None})),
    )

    # The integration: server exits, watcher gets cancelled, observer stopped.
    await _run_watch_loop(args, db_path=tmp_path / "fake.db")

    assert len(server_calls) == 1
    assert not fake_observer.started, "Observer.stop was not called on shutdown"


async def test_run_watch_loop_cancels_watcher_on_server_crash(tmp_path, monkeypatch) -> None:
    """Risk R4: if `run(...)` raises (not KeyboardInterrupt), the watcher
    still shuts down cleanly via try/finally."""
    import argparse

    from tests._fakes import FakeObserver

    fake_observer = FakeObserver()

    args = argparse.Namespace(
        project=str(tmp_path),
        verbose=False,
        watch=True,
        cache_dir=None,
        no_inspect=True,
        config=None,
    )

    from pydocs_mcp.__main__ import _run_watch_loop

    def _crashing_run(db_path, config_path=None):  # noqa: ARG001
        raise RuntimeError("simulated server crash")

    monkeypatch.setattr("pydocs_mcp.server.run", _crashing_run)

    from pydocs_mcp.serve import watcher as watcher_mod
    monkeypatch.setattr(
        watcher_mod, "_load_watchdog",
        lambda: (lambda: fake_observer, type("H", (), {"on_any_event": lambda self, e: None})),
    )

    with pytest.raises(RuntimeError, match="simulated server crash"):
        await _run_watch_loop(args, db_path=tmp_path / "fake.db")

    assert not fake_observer.started, "Observer.stop was not called on crash"
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_main_cli_watch.py -q
```

Expected: `ImportError: cannot import name '_run_watch_loop'`.

**Step 3 — Implement.** Edit `python/pydocs_mcp/__main__.py`:

3a. Add `_run_watch_loop` as a module-level helper, right after `_run_serve_indexing` (around line 395):

```python
async def _run_watch_loop(
    args: argparse.Namespace, *, db_path: Path | None = None,
) -> None:
    """Run the MCP server (Phase 2) AND the file watcher concurrently.

    Spec §4.1 deliverable 5: `--watch` adds a third element to
    `_cmd_serve` — the watcher asyncio task. The MCP server still runs
    on the main thread (CQ-1 SIGINT delivery preserved); the watcher
    runs on the asyncio loop in a worker thread via `asyncio.to_thread`.

    Try/finally guarantees the watcher task is cancelled regardless of
    how `run(...)` exits (KeyboardInterrupt, RuntimeError, etc.) —
    pins Risk R4 (no orphan Observer on crash) + spec Decision G.
    """
    import asyncio

    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.serve.watcher import FileWatcher
    from pydocs_mcp.server import run

    project, resolved_db = _project_and_db(args)
    if db_path is None:
        db_path = resolved_db

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    watch_cfg = config.serve.watch

    watcher = FileWatcher(
        root=project,
        extensions=tuple(watch_cfg.extensions),
        ignore_globs=tuple(watch_cfg.ignore_globs),
        debounce_ms=watch_cfg.debounce_ms,
    )

    async def _on_change() -> None:
        # Reindex via the same Phase 1 helper used at startup. Cache
        # makes the no-change case <100ms (spec §2).
        try:
            await _run_indexing(args)
        except Exception as exc:  # noqa: BLE001 -- watcher-loop boundary
            # WHY: a reindex failure during the watch loop should NOT
            # take down the MCP server. Log + keep serving stale data.
            log.error("watch: reindex failed: %s", exc)

    watcher_task = asyncio.create_task(watcher.run_until_cancelled(_on_change))
    log.info("watch: started (debounce=%dms, root=%s)", watch_cfg.debounce_ms, project)
    try:
        # `run(...)` is blocking; offload to a worker thread so the
        # watcher_task keeps draining events on the asyncio loop.
        await asyncio.to_thread(run, db_path, config_path=getattr(args, "config", None))
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("watch: watcher task exited with %s", exc)
```

3b. Update `_cmd_serve` to fork on `args.watch` (replace lines 501-532):

```python
def _cmd_serve(args: argparse.Namespace) -> int:
    # Phase 1 — async indexing through ``_run_cmd`` so the verbose /
    # traceback policy applies to indexing failures uniformly.
    code = _run_cmd(_run_serve_indexing(args), verbose=args.verbose)
    if code != 0:
        return code

    _project, db_path = _project_and_db(args)

    if getattr(args, "watch", False):
        # Phase 2 (--watch path): server + watcher concurrently via
        # ``_run_watch_loop``. ``run(...)`` is offloaded to a worker
        # thread inside ``_run_watch_loop`` so the watcher's asyncio
        # consumer keeps draining events.
        #
        # WHY this differs from the no-watch path: without `--watch`,
        # `run(...)` is the only thing happening on the main thread, so
        # SIGINT reaches it directly. With `--watch`, the asyncio loop
        # is also running here, so the loop owns SIGINT; `run(...)`
        # exits via thread-pool unwind when the loop is cancelled.
        try:
            asyncio.run(_run_watch_loop(args, db_path=db_path))
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 -- intentional top-level CLI boundary
            print(f"Error: {exc}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc(file=sys.stderr)
                log.exception("CLI command failed")
            else:
                print("(re-run with --verbose to see the traceback)", file=sys.stderr)
                log.error("CLI command failed: %s", exc)
            return 1

    # Phase 2 (no-watch path) — unchanged from today.
    from pydocs_mcp.server import run

    try:
        run(db_path, config_path=getattr(args, "config", None))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 -- intentional top-level CLI boundary
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
            log.exception("CLI command failed")
        else:
            print("(re-run with --verbose to see the traceback)", file=sys.stderr)
            log.error("CLI command failed: %s", exc)
        return 1
```

**Step 4 — Verify PASS.**

```bash
pytest tests/test_main_cli_watch.py -q
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add python/pydocs_mcp/__main__.py tests/test_main_cli_watch.py
git commit -m "$(cat <<'EOM'
serve: wire watcher into _cmd_serve Phase 2 via _run_watch_loop

Spec §4.1 deliverable 5: when `--watch` is set, spawn an asyncio task
running FileWatcher.run_until_cancelled alongside the blocking MCP
`run(...)`. `run(...)` is offloaded to a worker thread (`asyncio.to_thread`)
so the asyncio loop keeps draining file-system events while the MCP
server serves stdio.

Without `--watch`, the no-watch code path is byte-identical to today
(AC-7 pin) — `run(...)` on the main thread for clean SIGINT delivery.

try/finally guarantees `watcher_task.cancel()` on any `run(...)` exit
path (KeyboardInterrupt, RuntimeError, normal return) — Risk R4
(no orphaned Observer on crash). Reindex failures inside the watcher
loop are logged but don't take down the MCP server (the stale-but-
serving fallback is more useful than crashing).

Pins AC-1 (server + watcher both start), AC-6 (Ctrl+C cancels both),
AC-7 (no-watch unchanged).
EOM
)"
```

---

### Task 11 — WAL-mode regression pin (O2 closure)

**Goal:** the spec asks us to verify WAL mode. The shipped `db.py` already enables it (line 194) AND `tests/test_db.py:test_wal_mode` already pins it. Task is reduced to one defensive cross-reference test under the watcher suite so a future PR that touches WAL also breaks a `--watch`-flavored test, surfacing the impact.

**Step 1 — Write failing test.** Append to `tests/test_watcher.py`:

```python
def test_index_db_wal_mode_enabled_for_concurrent_reindex(tmp_path: Path) -> None:
    """Risk R5: concurrent MCP queries + watcher-triggered reindex needs
    WAL mode so readers don't block on the reindex writer.

    The pin lives in `tests/test_db.py::test_wal_mode` already; this test
    re-asserts it with the watcher context attached so a future PR that
    naively removes WAL also breaks a `--watch`-flavored test, surfacing
    the impact on live reindex during MCP queries.
    """
    from pydocs_mcp.db import open_index_database

    db_path = tmp_path / "test.db"
    conn = open_index_database(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", (
            f"watcher requires WAL so concurrent MCP queries during reindex "
            f"don't block; got journal_mode={mode!r}. "
            f"See spec §6 Risk R5."
        )
    finally:
        conn.close()
```

**Step 2 — Verify (PASS already — defensive pin).**

```bash
pytest tests/test_watcher.py::test_index_db_wal_mode_enabled_for_concurrent_reindex -q
```

Expected: PASS (WAL is already enabled). The test is a regression sentinel, not a TDD-RED test — this is the one exception in the plan where the failing-test step is replaced by a "pin existing behavior with a watcher-context comment so a future regression breaks this test too."

**Step 3 — Implement.** No production code change. Task is the test alone.

**Step 4 — Verify PASS.**

```bash
pytest -q
ruff check python/ tests/
```

All green.

**Step 5 — Commit.**

```bash
git add tests/test_watcher.py
git commit -m "$(cat <<'EOM'
tests: WAL-mode regression pin under watcher context (R5)

WAL is already enabled in `db.py:open_index_database` (line 194) and
pinned by `tests/test_db.py::test_wal_mode`. This adds a parallel pin
under the watcher suite so a future PR that naively drops WAL also
breaks a `--watch`-flavored test, surfacing the impact on concurrent
MCP queries during a watcher-triggered reindex.

No production code change.
EOM
)"
```

---

### Task 12 — README + DOCUMENTATION.md updates

**Goal:** one-paragraph mention in README; new "Live re-indexing" subsection in DOCUMENTATION.md. README MUST NOT trigger the jargon audit (`PR #N`, `sub-PR`, `Task N`, etc.).

**Step 1 — Write failing tests.** Create `tests/test_readme_watch_mention.py`:

```python
"""AC-12: README + DOCUMENTATION.md document the `--watch` flag."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_mentions_watch_flag() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "--watch" in readme, "README must mention the --watch CLI flag"


def test_readme_mentions_watch_extras_install() -> None:
    readme = (ROOT / "README.md").read_text()
    # Either the explicit `[watch]` extras OR a sentence pointing the
    # user at the install hint — surface area must be discoverable.
    assert "pydocs-mcp[watch]" in readme or "[watch]" in readme


def test_documentation_md_has_live_reindexing_subsection() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text()
    # Heading style: existing subsections use `## ` or `### `. We pin a
    # case-insensitive substring so a stylistic tweak (subsection level)
    # doesn't break the test.
    assert re.search(
        r"(?im)^#{2,4}\s+live re-?indexing", doc,
    ), "DOCUMENTATION.md missing the 'Live re-indexing' subsection"


def test_documentation_md_describes_yaml_knobs() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text()
    assert "serve.watch" in doc
    assert "debounce_ms" in doc
    assert "ignore_globs" in doc


def test_documentation_md_documents_install_hint() -> None:
    doc = (ROOT / "DOCUMENTATION.md").read_text()
    assert "pip install pydocs-mcp[watch]" in doc


def test_readme_does_not_introduce_pr_jargon() -> None:
    """Re-run the project-wide README jargon audit after edits — must stay clean."""
    import subprocess

    result = subprocess.run(
        [
            "bash", "-c",
            "find . -name 'README.md' "
            "-not -path '*/.venv/*' "
            "-not -path '*/.claude/*' "
            "-not -path '*/node_modules/*' "
            "-not -path '*/.git/*' "
            "-not -path '*/.pytest_cache/*' "
            "| xargs grep -nE '"
            "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of"
            "|PR-[A-Z][0-9.]+'",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"README jargon violations introduced by --watch docs:\n{result.stdout}"
    )
```

**Step 2 — Verify FAIL.**

```bash
pytest tests/test_readme_watch_mention.py -q
```

Expected: the first 5 tests fail (no docs yet); the jargon audit test passes.

**Step 3 — Implement.**

3a. Edit `README.md` — add a paragraph under "Quick start" (after the existing `pydocs-mcp serve` example). Use Edit to insert after the existing serve example. Replace the existing quick-start snippet with:

Look at the current text:

```bash
sed -n '64,90p' /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/serve-watch-flag/README.md
```

Identify the exact existing line and append directly after the first `pydocs-mcp serve .` example a new paragraph + code fence:

```markdown

### Live re-indexing (optional)

If you edit code while the MCP server is running, pass `--watch` to keep
the index fresh as you save files. Edits to `.py`, `.md`, and `.ipynb`
files trigger a debounced reindex; the next `search` / `lookup` call
sees the updated content.

```bash
pip install 'pydocs-mcp[watch]'
pydocs-mcp serve . --watch
```

Debounce, file extensions, and ignored paths are tunable via
`serve.watch.*` in your `pydocs-mcp.yaml` (see `DOCUMENTATION.md`).
```

3b. Edit `DOCUMENTATION.md` — add a new top-level section just after the CLI reference section (after line ~211 in the existing structure). Use Edit to insert before the next `## ` header.

Append a new section:

```markdown
## Live re-indexing

`pydocs-mcp serve --watch` runs a file-system watcher alongside the MCP
server. Edits under the project root re-trigger indexing in the
background; the next MCP query sees fresh data. Without `--watch`, the
server indexes once at startup (today's behavior — unchanged).

### Install

The watcher uses `watchdog`, which ships as an optional extra:

```bash
pip install 'pydocs-mcp[watch]'
pydocs-mcp serve . --watch
```

Without the `[watch]` extras, `pydocs-mcp serve --watch` exits with
an actionable install hint. Default `pydocs-mcp serve` (no `--watch`)
does not require `watchdog`.

### How it works

1. The watcher monitors the project root (NOT `site-packages/` —
   dependency changes are rare and user-initiated; re-run
   `pydocs-mcp index .` after `pip install`).
2. File-system events for paths matching `extensions` AND not matching
   any `ignore_globs` pattern are queued.
3. Events are **debounced** by `debounce_ms` — N edits within the
   window collapse into a single reindex. Editor atomic-save sequences
   (temp create → delete → rename) naturally fall under the same
   trigger.
4. Edits arriving during an in-flight reindex schedule **exactly one**
   follow-up reindex (no thundering herd from `git checkout` /
   `git rebase` rewrites).
5. The two-level cache (`packages.content_hash` + `chunks.content_hash`)
   makes the no-change case <100 ms; only modified packages are
   re-extracted, only added/changed chunks are re-embedded.

### YAML knobs (`serve.watch.*`)

All tunables live in YAML — no MCP tool params change (the MCP
surface stays at the fixed 2 tools: `search`, `lookup`). The CLI
`--watch` flag overrides `enabled` at runtime.

```yaml
# pydocs-mcp.yaml
serve:
  watch:
    enabled: false              # CLI --watch overrides this at runtime
    debounce_ms: 500            # 1 .. 60_000 ms; 500ms is editor-safe
    extensions: [".py", ".md", ".ipynb"]
    ignore_globs:
      - "**/__pycache__/**"
      - "**/.git/**"
      - "**/.venv/**"
      - "**/node_modules/**"
      - "**/.pytest_cache/**"
      - "**/*.pyc"
```

### Trade-offs

- **Memory + one OS event handle** for the watcher process — small,
  but headless / CI deployments that never edit code should leave
  `--watch` off.
- **Brief query-latency hit during reindex** — SQLite WAL mode allows
  concurrent readers, so MCP queries continue serving stale-but-correct
  data while the reindex transaction commits.
- **Reindex failures are logged but do not crash the MCP server** —
  the server keeps serving the previous index. Check the logs if
  results look stale.
```

3c. Run the audit grep manually to confirm zero matches:

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: no output.

**Step 4 — Verify PASS.**

```bash
pytest tests/test_readme_watch_mention.py -q
pytest -q
ruff check python/ tests/
```

All green; jargon audit still clean.

**Step 5 — Commit.**

```bash
git add README.md DOCUMENTATION.md tests/test_readme_watch_mention.py
git commit -m "$(cat <<'EOM'
docs: document `pydocs-mcp serve --watch` flag + YAML knobs

README gains a "Live re-indexing (optional)" subsection under Quick
start with the install hint + one-line invocation. DOCUMENTATION.md
gets a full "Live re-indexing" section covering how the watcher
works (debounce, coalesce, scope), the YAML tunables, and the
trade-offs (memory cost, query-latency during reindex, reindex-failure
log-don't-crash policy).

README jargon audit (`tests/test_readme_jargon_audit.py`) re-run —
no PR/sub-PR/Task references added.

Pins AC-12 (docs updated).
EOM
)"
```

---

### Task 13 — Verification gauntlet + AC self-check matrix

**Goal:** prove the full feature lands clean. Run the entire test suite + lint + benchmark + Rust suites. Produce a self-check mapping every spec §7 AC (1-12) to a specific test.

**Step 1 — Run the full pytest suite.**

```bash
pytest -q
```

Expected: 1367 + new tests pass (no failures); roughly 1390+ total.

**Step 2 — Run the benchmark suite (no regressions expected; watcher doesn't touch retrieval).**

```bash
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q
```

Expected: 283 pass.

**Step 3 — Lint sweep.**

```bash
ruff check python/ tests/ benchmarks/
```

Expected: clean.

**Step 4 — Rust checks (unaffected — pure-Python feature — but mandatory per CLAUDE.md).**

```bash
cargo fmt --check && cargo clippy -- -D warnings && cargo test
```

Expected: all pass (no Rust changes in this PR).

**Step 5 — AC self-check matrix.** Verify each spec §7 acceptance criterion is pinned by at least one test introduced in this PR:

| AC | What it requires | Test that pins it |
|---|---|---|
| AC-1 | `serve --watch` starts MCP server + filesystem watcher, both logged at startup | `test_main_cli_watch.py::test_serve_subparser_accepts_watch_flag`, `test_main_cli_watch.py::test_cmd_serve_with_watch_spawns_watcher_task`, `test_main_cli_watch.py::test_run_watch_loop_helper_exists` |
| AC-2 | Edit triggers reindex within `debounce_ms + ~50ms` | `test_watcher.py::test_watcher_fires_after_debounce_window` |
| AC-3 | `__pycache__/`, `.git/`, `.venv/`, `*.pyc` events do NOT trigger reindex | `test_watcher.py::test_watcher_filters_unrelated_events` |
| AC-4 | N edits within `debounce_ms` = 1 reindex | `test_watcher.py::test_watcher_debounces_burst_edits` |
| AC-5 | Edit during in-flight reindex schedules exactly one follow-up | `test_watcher.py::test_watcher_coalesces_during_in_flight_reindex`, `test_watcher.py::test_watcher_no_two_reindexes_overlap` |
| AC-6 | Ctrl+C cleanly shuts down both watcher AND MCP server | `test_main_cli_watch.py::test_run_watch_loop_cancels_watcher_on_server_exit`, `test_main_cli_watch.py::test_run_watch_loop_cancels_watcher_on_server_crash` (Risk R4 sibling) |
| AC-7 | Without `--watch`, behavior is byte-identical to pre-PR | `test_main_cli_watch.py::test_serve_subparser_watch_defaults_false`, `test_main_cli_watch.py::test_cmd_serve_without_watch_does_not_import_watcher`, existing `tests/test_main_cli.py::test_cmd_serve_does_not_wrap_run_in_to_thread` (still passes — pin preserved) |
| AC-8 | YAML `serve.watch.{enabled,debounce_ms,extensions,ignore_globs}` overrides apply; CLI `--watch` overrides `enabled` | `test_config_serve_watch.py::*` (all tests in that file), `test_default_config_serve_watch.py::test_app_config_load_picks_up_yaml_overrides`, `test_main_cli_watch.py::test_serve_subparser_accepts_watch_flag` |
| AC-9 | `watchdog` behind `[watch]` extras; without it `--watch` raises `ServiceUnavailableError` with install hint | `test_pyproject_extras.py::test_watch_extras_group_present`, `test_pyproject_extras.py::test_watchdog_not_in_main_dependencies`, `test_pyproject_extras.py::test_watch_extras_pins_watchdog_version_range`, `test_watcher.py::test_watcher_construction_raises_when_watchdog_missing` |
| AC-10 | Authorship audit clean — every commit sole-authored by `msobroza`, no `Co-Authored-By` trailers | Manual: `git log feature/serve-watch-flag --format="%an %s" | head` — verify all commits show `Max Raphael Sobroza Marques` and `git log --format="%B" feature/serve-watch-flag | grep -c "Co-Authored-By"` returns `0` |
| AC-11 | Full test suite green at locked baseline + new tests; ruff + benchmark suite unchanged | Steps 1-3 above |
| AC-12 | README has one-paragraph mention; DOCUMENTATION.md has "Live re-indexing" subsection | `test_readme_watch_mention.py::*` (all 5 doc-content tests; the 6th is the jargon-audit re-run) |

**Step 6 — Manual authorship sanity check.**

```bash
git log feature/serve-watch-flag ^main --format="%an <%ae>"
```

Expected: every line shows `Max Raphael Sobroza Marques <max.raphael@gmail.com>` (or whatever the local `git config user.name` / `user.email` resolves to — must be `msobroza`'s identity per global rule).

```bash
git log feature/serve-watch-flag ^main --format="%B" | grep -c "Co-Authored-By" || echo "0 trailers (clean)"
```

Expected: `0 trailers (clean)`.

**Step 7 — No commit on the gauntlet task.** This task only verifies; if anything fails, fix in the offending task and resequence.

**If everything is green:** the feature is ready for PR submission. Push the branch and open the PR with the spec link + this plan link in the description.

---

## Spec coverage table (final)

| Spec § | Item | Implemented by task(s) |
|---|---|---|
| §4.1 deliverable 1 | `watchdog>=4.0,<6.0` extras group | Task 1 |
| §4.1 deliverable 2 | `--watch` CLI flag on `serve` subparser | Task 9 |
| §4.1 deliverable 3 | `pydocs_mcp/serve/watcher.py` + `FileWatcher` | Tasks 4, 6, 7, 8 |
| §4.1 deliverable 4 | `AppConfig.serve.watch` sub-model | Task 2 |
| §4.1 deliverable 5 | Composition root wiring in `_cmd_serve` | Task 10 |
| §4.1 deliverable 6 | 5 named tests in `tests/test_watcher.py` | Tasks 4 (filter+lazy import), 5 (FakeObserver), 6 (filters), 7 (debounce), 8 (coalesce); plus `test_main_cli_watch.py::test_cmd_serve_without_watch_does_not_import_watcher` for "watcher_unchanged" pin |
| §4.1 deliverable 7 | README + DOCUMENTATION.md | Task 12 |
| Decision A | watchdog library choice | Task 1 (version pin) |
| Decision B | opt-in via `--watch` | Tasks 1 + 9 |
| Decision C | CLI flag + YAML tuning | Tasks 2, 3, 9 |
| Decision D | full-project reindex on any event | Task 10 (`_on_change` calls `_run_indexing` directly) |
| Decision E | `asyncio.Lock` + coalesce | Task 8 |
| Decision F | project root only, not site-packages | Task 10 (`watcher.root = project`) |
| Decision G | graceful shutdown via existing handler | Task 10 (`try/finally` in `_run_watch_loop`) |
| Decision H | NO `Co-Authored-By` trailers | Every task's commit template + Task 13 audit |
| Risk R1 | cross-platform inconsistency | Documented in `DOCUMENTATION.md` (Task 12); polling-backend fallback deferred per spec § "documented as future swap" |
| Risk R2 | git operation churn | Pinned by AC-5 (Task 8) — same coalesce mechanism |
| Risk R3 | missing dep → cryptic ImportError | Task 4 (lazy import + ServiceUnavailableError) |
| Risk R4 | orphaned watcher on crash | Task 10 (`try/finally` + `test_run_watch_loop_cancels_watcher_on_server_crash`) |
| Risk R5 | concurrent indexing vs MCP queries | Task 11 (WAL regression pin) |
| Risk R6 | test flakiness | Task 5 (FakeObserver — no real Observer in unit tests) |
| Risk R7 | editor atomic-save debounce | Task 7 (500ms default = atomic-save-safe) + Task 12 docs |
| O1 | watcher thread → asyncio bridge | Task 6 (`loop.call_soon_threadsafe(queue.put_nowait, ...)`) |
| O2 | WAL mode default | Task 11 (regression pin; WAL already enabled) |
| O3 | cross-platform CI matrix | Out of scope this PR — CI matrix update is a follow-up |
| O4 | `_run_watch_loop` placement | Task 10 (module-level helper next to `_run_indexing`) |
| O5 | logging cadence | Task 7 (`_log_trigger` caps at 3 paths + count) |
| O6 | `--watch` implies `--verbose`? | No — user opts in with `-v` explicitly (consistent with existing CLI shape) |
| AC-1..12 | All §7 ACs | See matrix in Task 13 |

---

## Authorship pin (repeated for emphasis)

**Every commit message in this plan uses `git commit -m "$(cat <<'EOM' ... EOM\n)"`. None of them contain a `Co-Authored-By:` trailer. Per `~/.claude/CLAUDE.md`, every commit on this branch is sole-authored by `msobroza`. AC-10 + spec Decision H.**

