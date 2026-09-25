"""The behind-upstream signal's value and its hand-off (spec §6.8b layer 1, #318).

The remote lane (``serve/remote_sync.py``) computes one :class:`UpstreamStatus`
per tracked branch OFF the request path — ``git rev-list --left-right --count``
against ``@{upstream}``, which reads the remote-tracking ref alone (no
``ls-remote``, no fetch) — and publishes the tuple on the bundle's
:class:`UpstreamStatusBoard`. The request path reads the last published tuple
through ``BranchDirectory.upstream_status_provider``: a callable returning a
tuple, never a git process (AC-31).

Application layer, not ``serve/``: the directory, the resolution and the
envelope read these values, and application code never imports ``serve``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.suggestions import (
    BEHIND_UPSTREAM_PULL,
    BEHIND_UPSTREAM_PULL_IN_ITS_WORKTREE,
    behind_upstream_fast_forward_command,
    behind_upstream_suggestion_text,
)

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400


class CheckoutPlace(StrEnum):
    """Where a branch is checked out, as the remote lane last read it — what
    decides the command that syncs it (#318 review)."""

    # The served working tree: ``git pull`` there syncs it.
    THIS_WORKTREE = "this_worktree"
    # Another worktree holds it: checked out, or being rebased or bisected.
    OTHER_WORKTREE = "other_worktree"
    # No worktree: only its ref can move.
    NOWHERE = "nowhere"


@dataclass(frozen=True, slots=True)
class UpstreamStatus:
    """One tracked branch against its upstream, as of the last fetch.

    ``fetched_at`` is the ``FETCH_HEAD`` mtime (epoch seconds), ``None`` when
    the repository was never fetched. ``checked_out`` picks the sync command
    the hint names; unknown reads as ``NOWHERE``, whose command git refuses
    for a checked-out branch.
    """

    branch: str
    upstream: str
    ahead: int
    behind: int
    fetched_at: float | None
    checked_out: CheckoutPlace = CheckoutPlace.NOWHERE


def _fetch_age(seconds: float) -> str:
    """``5m`` / ``2h`` / ``3d``: whole units, never ``0m`` (clock skew reads as 1m)."""
    if seconds >= _SECONDS_PER_DAY:
        return f"{int(seconds // _SECONDS_PER_DAY)}d"
    if seconds >= _SECONDS_PER_HOUR:
        return f"{int(seconds // _SECONDS_PER_HOUR)}h"
    return f"{max(1, int(seconds // _SECONDS_PER_MINUTE))}m"


def behind_upstream_suggestion(status: UpstreamStatus, now: float) -> str | None:
    """The ``behind_upstream`` rule's text, or ``None`` when nothing is to pull."""
    if status.behind <= 0:
        return None
    age = None if status.fetched_at is None else _fetch_age(now - status.fetched_at)
    return behind_upstream_suggestion_text(
        status.branch, status.upstream, status.behind, age, _sync_command(status)
    )


def _sync_command(status: UpstreamStatus) -> str:
    if status.checked_out is CheckoutPlace.THIS_WORKTREE:
        return BEHIND_UPSTREAM_PULL
    if status.checked_out is CheckoutPlace.OTHER_WORKTREE:
        return BEHIND_UPSTREAM_PULL_IN_ITS_WORKTREE
    return behind_upstream_fast_forward_command(status.branch, status.upstream)


def behind_upstream_hint_applies(config: AppConfig) -> bool:
    """The layer's own switch AND the ADR 0007 rule flag (#318): with every
    ``output.suggestions`` flag off ``meta.suggestion`` stays null (contract
    §2.3), and nothing computes a signal no response would carry."""
    return config.git.remote.behind_hint and config.output.suggestions.behind_upstream


def no_upstream_statuses() -> tuple[UpstreamStatus, ...]:
    """The provider of a bundle no remote lane serves (read-only loads, CLI queries)."""
    return ()


class UpstreamStatusBoard:
    """The last statuses one bundle's remote lane published.

    Written on the refresh loop's thread, read on the server's: publishing
    swaps one immutable tuple (a GIL-atomic attribute store), so a reader sees
    the previous tuple or the new one, never a mix.
    """

    __slots__ = ("_statuses",)

    def __init__(self) -> None:
        self._statuses: tuple[UpstreamStatus, ...] = ()

    def publish(self, statuses: Iterable[UpstreamStatus]) -> None:
        self._statuses = tuple(statuses)

    def latest(self) -> tuple[UpstreamStatus, ...]:
        return self._statuses


# WHY a process-wide registry (#318): the MCP server's routers and the refresh
# loop are two composition roots that share no object graph — plain ``serve``
# builds the loop on a thread of its own before the server exists — yet both
# serve ONE bundle in ONE process. Keyed by the bundle's resolved path, each
# side asks for the same board without threading it through either root.
_BOARDS: dict[str, UpstreamStatusBoard] = {}
_BOARDS_LOCK = threading.Lock()


def upstream_status_board_for(db_path: Path) -> UpstreamStatusBoard:
    """The board of the bundle at ``db_path``, created on first ask."""
    key = str(db_path.resolve())
    with _BOARDS_LOCK:
        return _BOARDS.setdefault(key, UpstreamStatusBoard())


__all__ = (
    "CheckoutPlace",
    "UpstreamStatus",
    "UpstreamStatusBoard",
    "behind_upstream_hint_applies",
    "behind_upstream_suggestion",
    "no_upstream_statuses",
    "upstream_status_board_for",
)
