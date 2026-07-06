"""The decision layer's only subprocess — a bounded ``git log`` reader (spec §D8).

``read_git_log`` shells out ONCE per reindex to dump a bounded window of commit
metadata, then ``_normalize_log`` reframes ``git``'s ``--name-only`` output into
the ``files ... ==END==`` line format the ``commit_messages`` parser reads. Both
are sync; the capture stage (a later slice) calls ``read_git_log`` via
``asyncio.to_thread`` so the subprocess never blocks the event loop.

Index-time only (never per-request): one subprocess spawn per reindex is cheap,
unlike the freshness probe which reads ``.git`` plumbing files because it runs
per response.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The per-commit header format. ``--name-only`` appends the touched paths on the
# lines after each formatted record; ``_normalize_log`` groups them under a
# ``files`` line and appends a ``==END==`` terminator. Fields the parser reads:
# ``commit <sha>`` / ``author-date <epoch>`` / ``subject <s>`` / ``body <b...>``.
_LOG_FORMAT = "commit %H%nauthor-date %at%nsubject %s%nbody %b%n"

# The four framed header fields, in emission order. A commit body (``body``) may
# span several lines; everything between ``body`` and the file list belongs to it.
_HEADER_PREFIXES = ("commit ", "author-date ", "subject ", "body ")


def read_git_log(project_root: Path, *, max_commits: int, timeout_seconds: float) -> str:
    """Bounded ``git log`` dump in the layer's line format, '' when unavailable.

    Index-time only (never per-request): a subprocess here costs one spawn per
    reindex, unlike the freshness probe which reads plumbing files because it
    runs per response. Any git failure (not a repo, git missing, timeout,
    non-zero exit) degrades to ``""`` so mining simply skips this source.
    """
    try:
        # S603/S607: resolving ``git`` on PATH is deliberate (the standard way
        # to invoke it); every argument is a bounded config int or the project
        # root, never user-controlled text, and ``check=True`` + the timeout keep
        # a hung/missing git from stalling the index.
        proc = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "git",
                "-C",
                str(project_root),
                "log",
                f"--max-count={max_commits}",
                "--name-only",
                f"--format={_LOG_FORMAT}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _normalize_log(proc.stdout)


def _normalize_log(stdout: str) -> str:
    """Reframe ``git log --name-only`` output into ``files ... ==END==`` records.

    ``git`` emits each commit as the four ``_LOG_FORMAT`` lines followed by the
    ``--name-only`` file list (a blank line separates header from files). This
    splits on the ``commit `` boundary, then per block keeps the header lines
    verbatim, collapses the trailing file paths onto one ``files`` line, and
    terminates the record with ``==END==``.
    """
    lines = stdout.splitlines()
    return "".join(_frame_block(block) for block in _split_blocks(lines))


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Group lines into per-commit blocks, each starting at a ``commit `` line."""
    blocks: list[list[str]] = []
    for line in lines:
        if line.startswith("commit "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _frame_block(block: list[str]) -> str:
    """One commit block → framed ``header ... files ... ==END==`` record."""
    header, files = _partition(block)
    file_line = "files " + " ".join(files)
    return "\n".join([*header, file_line]) + "\n==END==\n"


def _partition(block: list[str]) -> tuple[list[str], list[str]]:
    """Split a block into its header lines and its trailing file paths.

    Header runs until the blank line that ``git`` inserts before the file list;
    everything non-blank after that blank line is a touched path. A body with a
    trailing blank of its own is handled by taking the LAST blank run before the
    files as the separator (files never contain blanks).
    """
    header: list[str] = []
    files: list[str] = []
    in_files = False
    for line in block:
        if in_files:
            if line.strip():
                files.append(line.strip())
            continue
        if not line.strip():
            in_files = True  # blank separates header from the --name-only list
            continue
        header.append(line)
    return header, files
