"""Disk faults injected where the OS answers, so the code under test runs as it would.

A failed swap goes in at ``os.replace``, the call an atomic rewrite ends with,
rather than by faking the writer: the real rewrite runs up to the moment the
disk refuses it, which is what "a failing write leaves the file intact" is about.
"""

from __future__ import annotations

import errno


class FailingReplace:
    """``os.replace`` on a disk that fails: every swap is refused with an I/O error."""

    def __call__(self, source: object, target: object) -> None:
        raise OSError(errno.EIO, "Input/output error")
