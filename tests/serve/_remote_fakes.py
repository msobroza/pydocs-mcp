"""Fakes for the remote lane's tests (#318).

``RecordingGitRepository`` is the recording git adapter AC 1 asserts on: it
forwards every port call to a real or fake adapter and records the method
name, so a test states which git operations ran — and that no network one did.
``SleepsThenCancels`` is the lane's injected sleep: it records each wait and
cancels the lane after a set number, so the backoff is read off a list, never
waited for. The ``FakeGitRepository`` subclasses fail on cue the way the
subprocess adapter does, raising ``GitCommandError``. The builders below are
the scheduler and the configs the lane's test modules share.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.upstream_status import UpstreamStatusBoard
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.retrieval.config.git_models import RemoteConfig
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobQueue
from pydocs_mcp.serve.remote_sync import RemoteSyncScheduler
from tests._fakes import FakeGitRepository

A, B, C = "a" * 40, "b" * 40, "c" * 40

# The port calls that reach the network (spec §6.8b layer 3) and the two
# sanctioned repository writes (§6.8b layers 3 and 4).
NETWORK_CALLS = frozenset({"ls_remote_heads", "fetch"})
REPOSITORY_WRITES = frozenset({"fetch", "update_ref_if_unchanged"})
# A bound on work the lane hands a worker thread — never a timing assumption.
_DEADLINE_SECONDS = 10.0


async def no_pass(job: IndexJob) -> None:
    return None


def remote_config(**keys: object) -> RemoteConfig:
    return RemoteConfig.model_validate(keys)


def auto_fetch_config(**keys: object) -> RemoteConfig:
    """Layers 3 and 4 on, checking every 5 seconds."""
    return remote_config(
        auto_fetch={"enabled": True, "interval_seconds": 5},
        fast_forward_branches_without_worktree=True,
        **keys,
    )


def remote_scheduler(
    git: object,
    config: RemoteConfig,
    gitdir: Path,
    *,
    queue: IndexJobQueue | None = None,
    sleep: SleepsThenCancels | None = None,
    jitter: float = 0.0,
    tracked: tuple[str, ...] | Callable[[], tuple[str, ...]] = ("main", "feature/x"),
    board: UpstreamStatusBoard | None = None,
) -> RemoteSyncScheduler:
    return RemoteSyncScheduler(
        git=git,  # type: ignore[arg-type]
        config=config,
        queue=queue or IndexJobQueue(no_pass),
        gitdir=gitdir,
        tracked=tracked if callable(tracked) else lambda: tracked,
        board=board or UpstreamStatusBoard(),
        sleep=sleep or SleepsThenCancels(1),
        jitter=lambda: jitter,
    )


def moving_remote_git(cls: type[FakeGitRepository] = FakeGitRepository, **extra: object) -> Any:
    """``origin/main`` moved to B ahead of local ``main`` at A; ``feature/x`` did not."""
    return cls(
        remote_heads={"origin": (("main", B), ("feature/x", A))},
        refs={
            "refs/heads/main": A,
            "refs/heads/feature/x": A,
            "origin/main": B,
            "origin/feature/x": A,
        },
        upstreams={"main": "origin/main", "feature/x": "origin/feature/x"},
        ancestry={(A, B)},
        counts={("main", "origin/main"): (0, 1)},
        **extra,
    )


def logged_events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, object]]:
    """The structured log lines whose ``event`` is ``name``; other lines are skipped."""
    payloads = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == name:
            payloads.append(payload)
    return payloads


async def run_lane_until_it_cancels(scheduler: RemoteSyncScheduler) -> None:
    """The lane with auto-fetch on, until its injected sleep cancels it."""
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_until_cancelled()


async def until(condition: Callable[[], bool]) -> None:
    """Yield to the loop until ``condition`` holds: the lane's git reads run on a
    worker thread, and each yield lets their result land. No fixed wait."""
    deadline = time.monotonic() + _DEADLINE_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "the lane never got there"
        await asyncio.sleep(0)


class RecordingGitRepository:
    """A ``GitRepository`` that records each call's method name, then forwards it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return attribute(*args, **kwargs)

        return recorded


class SleepsThenCancels:
    """The lane's ``sleep``: records each wait; the ``allowed``-th one cancels."""

    def __init__(self, allowed: int) -> None:
        self.allowed = allowed
        self.durations: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)
        if len(self.durations) >= self.allowed:
            raise asyncio.CancelledError


@dataclass
class FlakyRemoteGit(FakeGitRepository):
    """``ls-remote`` fails ``failures`` times with ``stderr``, then answers."""

    failures: int = 0
    stderr: str = "fatal: unable to access 'https://example.invalid/': Could not resolve host"

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        if self.failures > 0:
            self.failures -= 1
            raise GitCommandError(("git", "ls-remote", "--heads", remote), "exit 128", self.stderr)
        return super().ls_remote_heads(remote)


@dataclass
class FetchFailsGit(FakeGitRepository):
    """``ls-remote`` answers; the fetch it asks for times out ``failures`` times."""

    failures: int = sys.maxsize

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise GitCommandError(("git", "fetch", remote), "timeout after 30s")
        super().fetch(remote, prune=prune)


@dataclass
class RefusingRefGit(FakeGitRepository):
    """``update-ref`` refuses ``refused`` as a held lock does: it raises (#306),
    it does not answer ``False`` (that is a lost race)."""

    refused: str = ""

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        if ref == self.refused:
            stderr = f"fatal: cannot lock ref '{ref}': Unable to create '{ref}.lock'"
            raise GitCommandError(("git", "update-ref", ref), "exit 128", stderr)
        return super().update_ref_if_unchanged(ref, new_sha, old_sha, message)


@dataclass
class UnreadableUpstreamGit(FakeGitRepository):
    """``rev-list`` fails for one branch (its upstream ref was pruned)."""

    unreadable: str = ""

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        if branch == self.unreadable:
            raise GitCommandError(("git", "rev-list", upstream), "exit 128", "fatal: bad revision")
        return super().ahead_behind(branch, upstream)
