"""``is_bundle_gone_error``: the storage errors a request path may degrade on (#311)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline.connection import CacheNotIndexedError
from pydocs_mcp.storage.errors import is_bundle_gone_error


@pytest.mark.parametrize(
    "exc",
    [
        CacheNotIndexedError(Path("/gone.db")),
        FileNotFoundError("/gone.db"),
        sqlite3.OperationalError("no such table: branches"),
        sqlite3.OperationalError("unable to open database file"),
    ],
    ids=["not-indexed", "file-not-found", "emptied", "unreadable"],
)
def test_a_removed_emptied_or_unreadable_bundle_is_gone(exc: BaseException) -> None:
    assert is_bundle_gone_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("no such column: landing_kind"),
        sqlite3.IntegrityError("no such table"),  # right text, wrong kind
        RuntimeError("no such table"),
    ],
    ids=["locked", "missing-column", "integrity", "not-sqlite"],
)
def test_any_other_error_is_a_defect_that_propagates(exc: BaseException) -> None:
    assert not is_bundle_gone_error(exc)
