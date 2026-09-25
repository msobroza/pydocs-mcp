"""The behind-upstream signal's hand-off (spec §6.8b layer 1, #318): the remote
lane publishes on the refresh loop's thread, the request path reads on the
server's, and both find one board per bundle."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from pydocs_mcp.application.upstream_status import (
    CheckoutPlace,
    UpstreamStatus,
    UpstreamStatusBoard,
    behind_upstream_suggestion,
    no_upstream_statuses,
    upstream_status_board_for,
)

STATUS = UpstreamStatus("main", "origin/main", 0, 2, None)
HOUR = 3600.0


def test_the_hint_names_the_count_and_the_fetch_age() -> None:
    here = UpstreamStatus(
        "feature/x", "origin/feature/x", 0, 3, 1000.0 - 2 * HOUR, CheckoutPlace.THIS_WORKTREE
    )
    assert behind_upstream_suggestion(here, now=1000.0) == (
        "[suggestion: branch 'feature/x' is behind origin/feature/x by 3 "
        "(last fetch 2h ago); run: git pull]"
    )
    minutes = replace(here, fetched_at=1000.0 - 300)
    assert "(last fetch 5m ago)" in (behind_upstream_suggestion(minutes, now=1000.0) or "")
    days = replace(here, fetched_at=1000.0 - 50 * HOUR)
    assert "(last fetch 2d ago)" in (behind_upstream_suggestion(days, now=1000.0) or "")
    assert behind_upstream_suggestion(replace(here, ahead=2, behind=0), now=1.0) is None


def test_the_sync_command_follows_where_the_branch_is_checked_out() -> None:
    """``git pull`` syncs the served working tree's branch only (#318 review):
    another worktree's branch is pulled there, and a branch no worktree holds
    is fast-forwarded to the remote-tracking ref the count was read against —
    which git refuses for a branch checked out anywhere."""
    commands = {
        place: behind_upstream_suggestion(replace(STATUS, checked_out=place), now=1.0)
        for place in CheckoutPlace
    }
    prefix = "[suggestion: branch 'main' is behind origin/main by 2; run: "
    assert commands == {
        CheckoutPlace.THIS_WORKTREE: prefix + "git pull]",
        CheckoutPlace.OTHER_WORKTREE: prefix + "git pull in the worktree that has it checked out]",
        CheckoutPlace.NOWHERE: prefix + "git fetch . origin/main:main]",
    }
    assert STATUS.checked_out is CheckoutPlace.NOWHERE  # what nobody said


def test_a_board_hands_over_the_last_published_tuple() -> None:
    board = UpstreamStatusBoard()
    assert board.latest() == () == no_upstream_statuses()
    board.publish([STATUS])
    assert board.latest() == (STATUS,)


def test_one_board_per_bundle_whichever_spelling_of_its_path(tmp_path: Path) -> None:
    """The server and the refresh loop are separate composition roots of one
    process: each asks for the bundle's board and must get the same one."""
    db = tmp_path / "proj.db"
    same = tmp_path / "sub" / ".." / "proj.db"
    (tmp_path / "sub").mkdir()
    upstream_status_board_for(db).publish((STATUS,))
    assert upstream_status_board_for(same).latest() == (STATUS,)
    assert upstream_status_board_for(tmp_path / "other.db").latest() == ()


def test_the_board_is_handed_out_once_under_concurrent_asks(tmp_path: Path) -> None:
    db, boards = tmp_path / "raced.db", []
    threads = [
        threading.Thread(target=lambda: boards.append(upstream_status_board_for(db)))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({id(board) for board in boards}) == 1
