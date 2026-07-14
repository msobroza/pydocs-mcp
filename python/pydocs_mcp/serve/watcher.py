"""File-system watcher for `pydocs-mcp serve --watch` (spec §4.1).

The MCP server runs on the main thread (Phase 2 of `_cmd_serve`); this
module runs alongside it as an asyncio task that consumes filesystem
events from `watchdog.Observer`'s native thread and re-triggers
indexing on debounce.

`watchdog` is a required runtime dependency; its import is still
deferred to `FileWatcher` construction (see `_load_watchdog`) so tests
can inject `FakeObserver` behavior and so importing this module stays
cheap for callers that pass `observer_factory=...` explicitly — a seam
for test injection and import-time cost, not extras gating.

Event-loop bridge: `watchdog.Observer` runs in its own native thread.
We give the event handler a reference to the asyncio loop + queue and
let it call `loop.call_soon_threadsafe(queue.put_nowait, path)` so
the consumer side sees the event on the right thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.project_toml import ProjectExcludes

log = logging.getLogger("pydocs-mcp.watch")


def _is_dependency_manifest(name: str) -> bool:
    """A ``pyproject.toml`` / ``requirements*.txt`` — files whose edits add or
    remove indexable dependencies.

    Mirrors :func:`pydocs_mcp.deps.list_dependency_manifest_files` so the watcher
    retriggers on exactly the files dependency discovery reads. Manifests match
    regardless of the configured ``extensions`` (adding a package must reindex),
    but still respect ``ignore_globs`` — a vendored ``pyproject.toml`` under an
    ignored ``.venv`` never fires.
    """
    return name == "pyproject.toml" or (name.startswith("requirements") and name.endswith(".txt"))


def _load_watchdog():
    """Resolve `watchdog.observers.Observer`.

    Kept as a seam (rather than a top-level import) for two reasons:
    tests monkeypatch `watcher_mod._load_watchdog` to inject
    `FakeObserver` behavior, and the deferred import keeps
    `import pydocs_mcp.serve.watcher` cheap for callers that construct
    `FileWatcher(observer_factory=...)` explicitly.

    `_Handler` (inside `run_until_cancelled`) uses duck typing —
    watchdog's `BaseObserver` invokes `handler.dispatch(event)` with no
    isinstance check — so the `FileSystemEventHandler` class is
    deliberately not imported.
    """
    from watchdog.observers import Observer

    return Observer


def _no_derived_globs() -> tuple[str, ...]:
    """Default ``derived_globs_provider`` — no user-exclude globs derived."""
    return ()


def derive_exclude_globs(excludes: ProjectExcludes, project_root: Path) -> tuple[str, ...]:
    """Translate user exclusion entries into watchdog ignore globs (spec §7.6).

    Bare names become ``<project_root>/**/<name>/**`` and anchored entries
    become ``<project_root>/<path>/**``. Both are prefixed with the absolute
    project root because ``FileWatcher._matches`` fnmatches the FULL absolute
    path string: an unanchored ``**/<name>/**`` would match ancestor
    components of the project root's own path (a project at
    ``/home/user/docs/myproj`` excluding ``"docs"`` would silence every
    event under the root, including the root ``pyproject.toml`` — no event,
    no reindex, ever). Root-anchoring puts the wildcard segment strictly
    below the root.

    Best-effort churn suppression only (spec decision D6): fnmatch's ``*``
    is not a globstar, so a bare-name glob misses a top-level occurrence
    (``<root>/<name>/...`` has no ``/<name>/`` after a below-root segment).
    That miss costs one cheap cached reindex per event — discovery owns
    correctness, never this derivation.

    Sorted for deterministic output (frozenset iteration order varies).
    """
    root = str(project_root)
    bare = tuple(f"{root}/**/{name}/**" for name in sorted(excludes.names))
    anchored = tuple(f"{root}/{path}/**" for path in sorted(excludes.anchored))
    return bare + anchored


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
    # WHY a provider callable, not a second globs tuple: derived (user-
    # exclude) globs must be swappable after a manifest-triggered reindex
    # (spec D6 shrink direction, AC-25) while this dataclass is frozen.
    # `_matches` re-reads the provider on every event; the composition
    # root (`_build_watcher_and_callback`) swaps the backing value.
    # Same injected-callable pattern as `observer_factory`.
    derived_globs_provider: Callable[[], tuple[str, ...]] = field(default=_no_derived_globs)

    def __post_init__(self) -> None:
        # WHY: resolve the watchdog import at construction time rather
        # than at first event — startup failure is easier to diagnose
        # than mid-run "why isn't my watcher firing".
        if self.observer_factory is None:
            object.__setattr__(self, "observer_factory", _load_watchdog())
        # WHY: `_matches` lowercases the FILE's suffix (`path.suffix.lower()`)
        # but `path.suffix` always includes the leading dot — a configured
        # extension that is uppercase (`.PY`) or missing the dot (`py`) would
        # never equal it, silently disabling watching for every source file
        # (only manifests would still fire) with zero feedback to the user.
        # Normalizing here keeps configured extensions symmetric with the
        # file-suffix case-insensitivity already documented on `_matches`.
        normalized = tuple(
            ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in self.extensions
        )
        object.__setattr__(self, "extensions", normalized)

    def _matches(self, path: Path) -> bool:
        """Pure-function event filter — returns True iff the path is
        a candidate for triggering a reindex.

        Extensions are compared case-insensitively (path.suffix.lower())
        so editors that save as `Setup.PY` on case-insensitive filesystems
        (macOS APFS / Windows NTFS by default) still trigger reindex.
        Defaults in WatchConfig are lowercase by convention.

        Dependency manifests (`pyproject.toml` / `requirements*.txt`) always
        match regardless of `extensions`, so adding a package to them retriggers
        indexing and the new dependency gets picked up.

        Returns False for: non-watched extensions that aren't a manifest, paths
        matching any `ignore_globs` pattern OR any glob currently returned by
        `derived_globs_provider` (user-exclude suppression, spec §7.6).
        """
        if path.suffix.lower() not in self.extensions and not _is_dependency_manifest(path.name):
            return False
        path_str = str(path)
        patterns = self.ignore_globs + self.derived_globs_provider()
        return not any(fnmatch.fnmatch(path_str, pattern) for pattern in patterns)

    async def run_until_cancelled(
        self,
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """Start the observer, consume filtered events, fire `on_change`.

        Cancelling the parent task stops the observer and unwinds cleanly.
        See spec Decisions E + G.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Path] = asyncio.Queue()

        watcher_self = self

        # Plain class (no FileSystemEventHandler parent): watchdog's
        # BaseObserver dispatches every event via `handler.dispatch(event)`,
        # so implementing `dispatch` directly satisfies the REAL contract
        # while keeping the test path watchdog-free when callers pass
        # `observer_factory=FakeObserver`. (A handler with only
        # `on_any_event` dies with AttributeError in the emitter thread —
        # watch mode then silently never fires.)
        class _Handler:
            def dispatch(self, event) -> None:
                # WHY: `watchdog` calls this from its own native thread.
                # `loop.call_soon_threadsafe(queue.put_nowait, ...)` is the
                # documented bridge — never `queue.put_nowait` directly,
                # which would race the asyncio side.
                #
                # Moved events (atomic-save editors: write `app.py.tmp`,
                # rename over `app.py`) carry the real file only in
                # `dest_path`; non-move events default it to "". Consult
                # BOTH ends so a tmp→real rename triggers reindex and a
                # real→elsewhere rename (content removed) does too.
                candidates = [Path(event.src_path)]
                dest = getattr(event, "dest_path", "")
                if dest:
                    candidates.append(Path(dest))
                matched = [p for p in candidates if watcher_self._matches(p)]
                if not matched:
                    return
                # Loop closed (observer being torn down): drop the event.
                with contextlib.suppress(RuntimeError):
                    for path in matched:
                        loop.call_soon_threadsafe(queue.put_nowait, path)

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
        """Consume queued events; debounce + coalesce per spec Decision E.

        Concurrency model:
        - Only one `on_change()` runs at a time (`reindex_lock`).
        - Events arriving while the lock is held set `pending["flag"]=True`
          so a single follow-up reindex fires after the current one releases.
        - Burst events during an in-flight reindex coalesce to ONE
          follow-up regardless of count (AC-5).
        """
        debounce_s = self.debounce_ms / 1000.0
        reindex_lock = asyncio.Lock()
        # Mutable closure state — `_consume` is the single async consumer,
        # so no cross-task aliasing concerns. The `_trigger_with_followup`
        # coroutine is scheduled via `asyncio.create_task` from this same
        # consumer, so writes are serialized through the event loop.
        #
        # `deferred_paths` accumulates the paths from every trigger that
        # arrived while a reindex was in flight — the follow-up reindex
        # then carries the full path list into `_log_trigger`, so the
        # operator's log line names the files that motivated the
        # follow-up (not an empty list).
        deferred_paths: list[Path] = []
        # Strong refs to spawned trigger tasks — without holding these,
        # the event loop may garbage-collect a pending task and emit
        # "Task was destroyed but it is pending" warnings. The set
        # discards each task when it completes via `add_done_callback`.
        bg_tasks: set[asyncio.Task] = set()

        try:
            while True:
                first_path = await queue.get()
                pending_paths: list[Path] = [first_path]
                while True:
                    try:
                        nxt = await asyncio.wait_for(queue.get(), timeout=debounce_s)
                        pending_paths.append(nxt)
                    except TimeoutError:
                        break
                # Fire-and-forget so the consumer loop can immediately resume
                # draining queue events while the current reindex runs. Hold a
                # strong ref in `bg_tasks` to prevent the event loop from
                # GC-ing the pending task.
                task = asyncio.create_task(
                    self._run_trigger(
                        pending_paths,
                        reindex_lock=reindex_lock,
                        deferred_paths=deferred_paths,
                        on_change=on_change,
                    ),
                    name="watcher-trigger",
                )
                bg_tasks.add(task)
                task.add_done_callback(bg_tasks.discard)
        finally:
            # WHY: `run_until_cancelled` cancellation (server shutdown mid-
            # reindex) must not leave a "watcher-trigger" task running
            # unsupervised after this coroutine returns — it would keep
            # writing SQLite until interpreter/loop teardown cancels it
            # mid-transaction, or get GC'd with a "Task was destroyed but
            # it is pending" warning. Cancel + await every still-running
            # trigger task here so shutdown has a defined order: in-flight
            # reindex is cancelled and observed before `_consume` returns.
            for bg_task in bg_tasks:
                bg_task.cancel()
            if bg_tasks:
                await asyncio.gather(*bg_tasks, return_exceptions=True)

    async def _run_trigger(
        self,
        paths: list[Path],
        *,
        reindex_lock: asyncio.Lock,
        deferred_paths: list[Path],
        on_change: Callable[[], Awaitable[None]],
    ) -> None:
        """One fired trigger: reindex ``paths``, then drain any paths that
        arrived mid-flight. Extracted from :meth:`_consume` so the consumer
        loop stays under the cognitive-complexity gate; scheduled
        fire-and-forget (one task per debounced burst).

        ``reindex_lock`` / ``deferred_paths`` are owned by the ``_consume``
        frame and shared by reference — safe because ``_consume`` is the
        single async consumer and every write here is serialized through the
        event loop.
        """
        # If a reindex is in flight, accumulate the paths so the in-flight
        # reindex's post-fire drain carries them into the follow-up log line.
        if reindex_lock.locked():
            deferred_paths.extend(paths)
            log.debug("watch: in-flight reindex; queued follow-up")
            return

        async def _drain_guarded(batch: list[Path]) -> None:
            # `on_change` (e.g. `_run_indexing`) can raise — a transient sqlite
            # "database is locked" is the canonical case. A raise must NOT skip
            # draining `deferred_paths` (those paths would sit stranded until an
            # unrelated future event, silently losing edits in an idle
            # workspace) or leave the task exception unretrieved. Guarding every
            # drain keeps the `while` loop below running regardless of failures.
            self._log_trigger(batch)
            try:
                await on_change()
            except Exception:
                log.exception("watch: reindex failed")

        async with reindex_lock:
            await _drain_guarded(paths)
            # `while` (not `if`): a continuously-edited workspace can queue more
            # events DURING the follow-up reindex itself; keep draining until
            # idle so we don't silently miss a burst that lands mid-lock.
            while deferred_paths:
                follow_up = deferred_paths.copy()
                deferred_paths.clear()
                log.info("watch: in-flight follow-up reindex firing")
                await _drain_guarded(follow_up)

    def _log_trigger(self, paths: list[Path]) -> None:
        """Log the trigger paths (cap at 3 + a count to keep logs sane).

        Spec Open Item O5 — INFO line per trigger with up to 3 changed
        paths. Larger bursts (editor save-all, git checkout) collapse
        into `(+N more)` suffix so the log stays readable.
        """
        if not paths:
            return
        head = ", ".join(str(p) for p in paths[:3])
        if len(paths) > 3:
            log.info("watch: reindex triggered (%s, +%d more)", head, len(paths) - 3)
        else:
            log.info("watch: reindex triggered (%s)", head)
