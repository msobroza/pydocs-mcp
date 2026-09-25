"""The remote lane while the remote is unreachable (spec §6.8b offline rules,
#318): backoff with jitter, failures classified, each state change logged once,
the request path told nothing, a failed fetch retried — and the local lanes
never held. Fakes only, on an injected sleep and jitter."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from pydocs_mcp.application.upstream_status import UpstreamStatusBoard
from pydocs_mcp.serve.index_jobs import (
    LOCAL_BRANCH_PRIORITY,
    IndexJob,
    IndexJobKind,
    IndexJobQueue,
)
from pydocs_mcp.serve.remote_sync import RemoteSyncState
from tests._fakes import FakeGitRepository
from tests.serve._remote_fakes import (
    FetchFailsGit,
    FlakyRemoteGit,
    SleepsThenCancels,
    auto_fetch_config,
    logged_events,
    moving_remote_git,
    no_pass,
    remote_config,
    remote_scheduler,
    run_lane_until_it_cancels,
)

_AUTHENTICATION_STDERR = "fatal: Authentication failed for 'https://example.invalid/'"


def _backoff(interval: int, ceiling: int):
    return remote_config(
        auto_fetch={"enabled": True, "interval_seconds": interval, "backoff_max_seconds": ceiling}
    )


async def test_offline_backoff_logs_once_and_never_touches_local_jobs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    git = FakeGitRepository(fail=True)
    queue, sleep = IndexJobQueue(no_pass), SleepsThenCancels(3)
    scheduler = remote_scheduler(git, _backoff(10, 25), tmp_path, queue=queue, sleep=sleep)
    with caplog.at_level(logging.DEBUG, logger="pydocs-mcp"):
        await run_lane_until_it_cancels(scheduler)
    assert sleep.durations == [10.0, 20.0, 25.0]
    assert scheduler.state is RemoteSyncState.OFFLINE
    offline = logged_events(caplog, "remote_sync_offline")
    assert len(offline) == 1 and offline[0]["remote"] == "origin"
    assert offline[0]["authentication"] is False
    assert queue.snapshot() == ()


async def test_an_authentication_failure_backs_off_to_the_ceiling_at_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    git = FlakyRemoteGit(failures=5, stderr=_AUTHENTICATION_STDERR)
    sleep = SleepsThenCancels(2)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await run_lane_until_it_cancels(
            remote_scheduler(git, _backoff(10, 900), tmp_path, sleep=sleep)
        )
    assert sleep.durations == [900.0, 900.0]
    (offline,) = logged_events(caplog, "remote_sync_offline")
    assert offline["authentication"] is True and "credential" in str(offline["hint"])


async def test_an_authentication_failure_after_a_network_one_still_names_its_fix(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#318 review: VPN down, then the credentials expire. Only the second
    failure has a fix to name, so the class change is logged — once."""
    git = FlakyRemoteGit(failures=4)
    scheduler = remote_scheduler(git, _backoff(10, 900), tmp_path)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await scheduler.check_remote_once()
        git.stderr = _AUTHENTICATION_STDERR
        await scheduler.check_remote_once()
        await scheduler.check_remote_once()
    offline = logged_events(caplog, "remote_sync_offline")
    assert [entry["authentication"] for entry in offline] == [False, True]
    assert "credential" in str(offline[1]["hint"])
    assert scheduler.state is RemoteSyncState.OFFLINE


async def test_recovery_logs_once_and_resets_the_interval(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    git = FlakyRemoteGit(failures=2)
    sleep = SleepsThenCancels(4)
    scheduler = remote_scheduler(git, _backoff(10, 100), tmp_path, sleep=sleep)
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await run_lane_until_it_cancels(scheduler)
    assert sleep.durations == [10.0, 20.0, 10.0, 10.0]
    assert len(logged_events(caplog, "remote_sync_offline")) == 1
    assert len(logged_events(caplog, "remote_sync_online")) == 1
    assert scheduler.state is RemoteSyncState.ONLINE


async def test_jitter_shortens_a_backoff_wait_and_never_an_online_one(tmp_path: Path) -> None:
    offline_sleep, online_sleep = SleepsThenCancels(2), SleepsThenCancels(1)
    flaky = FlakyRemoteGit(failures=5)
    await run_lane_until_it_cancels(
        remote_scheduler(flaky, _backoff(10, 100), tmp_path, sleep=offline_sleep, jitter=1.0)
    )
    await run_lane_until_it_cancels(
        remote_scheduler(
            FakeGitRepository(), _backoff(10, 100), tmp_path, sleep=online_sleep, jitter=1.0
        )
    )
    assert offline_sleep.durations == [9.0, 18.0]  # never above the doubled wait or the ceiling
    assert online_sleep.durations == [10.0]


async def test_while_offline_the_request_path_is_told_nothing(tmp_path: Path) -> None:
    """Spec §6.8b offline rule: no suggestion tells the agent to pull while the
    remote is unreachable; the last statuses come back on recovery."""
    git = moving_remote_git(FlakyRemoteGit)
    board = UpstreamStatusBoard()
    scheduler = remote_scheduler(git, auto_fetch_config(), tmp_path, tracked=("main",), board=board)
    scheduler.refresh_upstream_status()
    known = board.latest()
    assert [s.behind for s in known] == [1]
    git.failures = 1
    await scheduler.check_remote_once()
    assert (scheduler.state, board.latest()) == (RemoteSyncState.OFFLINE, ())
    await scheduler.check_remote_once()
    assert scheduler.state is RemoteSyncState.ONLINE and board.latest() == known


async def test_a_failed_fetch_is_retried_on_the_next_check(tmp_path: Path) -> None:
    """#318 review: the heads a check saw count as known only once their fetch
    succeeded — else the next ls-remote matches them, nothing is fetched until
    the remote moves again, and the index stays behind without an error."""
    git = moving_remote_git(FetchFailsGit, failures=1)
    scheduler = remote_scheduler(git, auto_fetch_config(), tmp_path, tracked=("main",))
    await scheduler.check_remote_once()
    assert (scheduler.state, git.fetch_calls) == (RemoteSyncState.OFFLINE, [])
    await scheduler.check_remote_once()
    assert (scheduler.state, git.fetch_calls) == (RemoteSyncState.ONLINE, [("origin", True)])


async def test_a_fetch_failure_is_logged_and_the_local_pass_still_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AC 3 (spec §6.8b "local first"): the lane is not a queue job, so a fetch
    that fails neither delays nor fails the local pass queued beside it."""
    ran: list[str] = []

    async def runner(job: IndexJob) -> None:
        ran.append(job.branch)

    queue = IndexJobQueue(runner)
    drain = asyncio.create_task(queue.run_until_cancelled())
    git = moving_remote_git(FetchFailsGit)
    scheduler = remote_scheduler(git, auto_fetch_config(), tmp_path, queue=queue)
    await queue.submit(IndexJob(IndexJobKind.BRANCH_INDEX, "main", priority=LOCAL_BRANCH_PRIORITY))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await scheduler.check_remote_once()
    await queue.wait_idle()
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert ran == ["main"]
    (offline,) = logged_events(caplog, "remote_sync_offline")
    assert "timeout after 30s" in str(offline["reason"])
    assert scheduler.state is RemoteSyncState.OFFLINE and git.updated_refs == []
