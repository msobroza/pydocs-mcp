"""The git adapter's error type — raised at the subprocess boundary only."""

from __future__ import annotations

from pydocs_mcp.exceptions import PydocsMCPError


class GitCommandError(PydocsMCPError, RuntimeError):
    """A ``git`` subprocess failed, timed out, or could not start.

    Raised only inside ``pydocs_mcp.git``; application code sees this type,
    never ``subprocess`` errors (spec §6.14 item 7). ``argv`` is the exact
    command, ``reason`` the failure class ("timeout after 30s", "exit 128",
    "binary not found"), ``stderr_tail`` the last lines git printed.
    """

    def __init__(self, argv: tuple[str, ...], reason: str, stderr_tail: str = "") -> None:
        self.argv = argv
        self.reason = reason
        self.stderr_tail = stderr_tail
        detail = f": {stderr_tail}" if stderr_tail else ""
        super().__init__(f"git command {' '.join(argv)!r} failed ({reason}){detail}")
