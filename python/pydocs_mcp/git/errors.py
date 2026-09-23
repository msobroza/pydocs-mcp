"""The git adapter's error type — raised at the subprocess boundary only."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from pydocs_mcp.exceptions import PydocsMCPError


class GitCommandError(PydocsMCPError, RuntimeError):
    """A ``git`` subprocess failed, timed out, or could not start.

    Raised only inside ``pydocs_mcp.git``; application code sees this type,
    never ``subprocess`` errors (spec §6.14 item 7). ``argv`` is the exact
    command (a two-process pipe's timeout names both, joined by ``"|"``),
    ``reason`` the failure class ("timeout after 30s", "exit 128",
    "binary not found"), ``stderr_tail`` the last lines git printed.
    """

    def __init__(self, argv: tuple[str, ...], reason: str, stderr_tail: str = "") -> None:
        self.argv = argv
        self.reason = reason
        self.stderr_tail = stderr_tail
        detail = f": {stderr_tail}" if stderr_tail else ""
        super().__init__(f"git command {' '.join(argv)!r} failed ({reason}){detail}")


@contextmanager
def translate_git_start_failures(argv: tuple[str, ...]) -> Iterator[None]:
    """Re-raise a failure to START ``argv`` as :class:`GitCommandError` (spec §6.14 item 7)."""
    try:
        yield
    except FileNotFoundError as exc:
        raise GitCommandError(argv, "binary not found") from exc
    except OSError as exc:
        raise GitCommandError(argv, f"could not start: {exc}") from exc


@contextmanager
def translate_git_timeout(argv: tuple[str, ...], timeout: float) -> Iterator[None]:
    """Re-raise ``subprocess.TimeoutExpired`` as :class:`GitCommandError` — the one reason format."""
    try:
        yield
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(argv, f"timeout after {timeout:g}s") from exc
