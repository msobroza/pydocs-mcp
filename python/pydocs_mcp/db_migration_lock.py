"""One bundle, several processes: the schema migration runs under the write lock.

Two processes can open one bundle at once — two MCP sessions starting servers on
the same project, ``index`` run while ``serve`` starts, a multi-repo workspace
loaded twice. The v17 → v18 step copies three tables and holds SQLite's write
lock for seconds on a large bundle, past a connection's ordinary busy wait. A
second opener that had read the old stamp without the lock used to hit
SQLITE_BUSY mid-sweep, take it for schema drift and rebuild the bundle from
scratch, wiping the rows the first process had just migrated (#305 review).

So a due migration first takes the write lock (``BEGIN IMMEDIATE``, waiting up to
:data:`MIGRATION_LOCK_WAIT_MS`) and reads the stamp AGAIN under it: a second
opener waits, then sees the new stamp and runs only the no-op repair sweep. An
up-to-date bundle opens without the lock, so a starting server never waits on an
index pass's write transaction (and a read-only file still opens). And an error
about the environment rather than the schema is raised, never "repaired" by a
rebuild — see :func:`is_environmental_sqlite_error`.

Imports nothing from the package: ``db.py`` imports this module at load time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Long enough for the v17 → v18 copy of a very large bundle (a million references
# took ~20 s in review); past it the open fails loudly with the bundle untouched
# instead of hanging a server's startup indefinitely.
MIGRATION_LOCK_WAIT_MS = 600_000

# Lock contention, a full disk, an I/O failure, a read-only file: the environment,
# not the schema. A rebuild cannot heal any of them, and its DROPs would destroy a
# bundle that is intact. Primary result codes; extended ones are masked down.
_ENVIRONMENTAL_RESULT_CODES = frozenset(
    {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_READONLY,
    }
)
_PRIMARY_RESULT_CODE_MASK = 0xFF


def read_user_version(conn: sqlite3.Connection) -> int:
    """The bundle's schema stamp (``PRAGMA user_version``)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def lock_bundle_for_migration(conn: sqlite3.Connection, path: Path) -> int:
    """Take the bundle's write lock and return its stamp as read under it.

    The lock is held by the transaction this opens; the caller's commit (or
    rollback) releases it. Waits up to :data:`MIGRATION_LOCK_WAIT_MS` for another
    process's migration, then restores the connection's ordinary busy wait.
    """
    ordinary_wait_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute(f"PRAGMA busy_timeout = {MIGRATION_LOCK_WAIT_MS}")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        # Generic on purpose: a read-only file fails here too, not only a
        # migration in another process that outlasted the wait.
        exc.add_note(
            f"{path}: could not take the write lock to migrate (waited up to "
            f"{MIGRATION_LOCK_WAIT_MS} ms); the bundle is untouched"
        )
        raise
    finally:
        conn.execute(f"PRAGMA busy_timeout = {ordinary_wait_ms}")
    return read_user_version(conn)


def is_environmental_sqlite_error(exc: sqlite3.Error) -> bool:
    """True when ``exc`` is about the environment, not the schema.

    An exception built in Python carries no result code; it reads as schema
    trouble, which keeps the open's rebuild-on-drift fallback as it was.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None:
        return False
    return (code & _PRIMARY_RESULT_CODE_MASK) in _ENVIRONMENTAL_RESULT_CODES
