"""Read-only workspace bundle scan.

Reads each project's name and dependency packages straight from its ``*.db``
bundle over a ``mode=ro`` connection. It deliberately does NOT go through
``pydocs_mcp.multirepo.discover_workspace`` / ``open_index_database``: those
open bundles read-write and may migrate or even rebuild them, so a UI-side
scan through that path could mutate (or wipe) the user's index just by
listing it. Names/packages here still match what the server's ``project=`` /
``package=`` filters expect.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

# Cache files are named ``{project}_{md5[:10]}.db`` (pydocs_mcp.multirepo).
_SLUG_RE = re.compile(r"^(.*)_[0-9a-f]{10}$")


def _ro_uri(db: Path) -> str:
    # Path.as_uri() percent-encodes %, ?, #, spaces — so appending the query is
    # safe even for a workspace path like ~/indexes/C#-port/.
    return db.resolve().as_uri() + "?mode=ro"


def _scalar(conn: sqlite3.Connection, sql: str) -> object | None:
    """First column of the first row, or None if the table/row is absent."""
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return None  # pre-v11 bundle: no index_metadata table
    return row[0] if row else None


def _project_name(conn: sqlite3.Connection, db: Path) -> str:
    name = _scalar(conn, "SELECT project_name FROM index_metadata LIMIT 1")
    if name:
        return str(name)
    m = _SLUG_RE.match(db.stem)  # legacy bundle: recover from the filename
    return m.group(1) if m else db.stem


def workspace_catalog(workspace: str) -> dict[str, list[str]]:
    """Map each indexed project to its dependency packages (own code excluded).

    On duplicate project names the newest bundle wins, mirroring the server's
    ``max(indexed_at)`` routing (and, like it, keeping the first on a tie).
    """
    best: dict[str, tuple[float, list[str]]] = {}
    for db in sorted(Path(workspace).expanduser().glob("*.db")):
        with closing(sqlite3.connect(_ro_uri(db), uri=True)) as conn:
            name = _project_name(conn, db)
            indexed_at = float(
                _scalar(conn, "SELECT indexed_at FROM index_metadata LIMIT 1") or 0.0
            )
            packages = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM packages WHERE name != '__project__' ORDER BY name"
                )
            ]
        if name not in best or indexed_at > best[name][0]:
            best[name] = (indexed_at, packages)
    return {name: packages for name, (_, packages) in sorted(best.items())}


def render_catalog(catalog: dict[str, list[str]]) -> str:
    """One line per project, naming the exact project=/package= filter values."""
    return "\n".join(
        f"- {name} — dependency packages: {', '.join(packages)}"
        if packages
        else f"- {name} — own code only (no dependency packages indexed)"
        for name, packages in catalog.items()
    )
