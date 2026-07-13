"""Multi-repo db resolution — discover + name + select pre-built ``.db`` bundles.

A single MCP server can serve several already-indexed projects. Each project is a
portable ``{name}_{hash}.db`` (+ its ``.tq`` sidecar) that may live anywhere —
not necessarily beside the real source. This module answers "which dbs, under
what project names" so the composition root can build one backend per project and
route a query by ``project=`` scope.

Loading is startup-config only (CLI ``--db`` / ``--workspace``); the per-query
``project`` filter selects among what was loaded. A workspace/explicit-db load is
READ-ONLY — the real source may be absent, so reindex/watch is disabled (an
absent project would otherwise re-index itself to empty).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from pydocs_mcp.storage.index_metadata import IndexMetadata, read_index_metadata

# ``cache_path_for_project`` names files ``{project_name}_{md5[:10]}.db``; the
# 10-hex-char suffix is the path slug. Strip it to recover the project name when
# a db predates the stamped ``index_metadata.project_name``.
_SLUG_RE = re.compile(r"^(.*)_[0-9a-f]{10}$")


class FutureSchemaError(RuntimeError):
    """A bundle's ``PRAGMA user_version`` is newer than this build understands.

    Multi-repo serving is READ-ONLY over a portable bundle it doesn't own
    (see module docstring). ``open_index_database``'s migration ladder maps
    any version it doesn't recognize to ``_rebuild_from_scratch`` — correct
    for the write-side indexing cache ("derived data, prefer working over
    bricked"), but catastrophic here: it would silently drop every table in
    a bundle built by a NEWER pydocs-mcp before this module ever gets to read
    it. Detecting the future version BEFORE delegating to
    ``open_index_database`` keeps the bundle untouched on disk.
    """


def _check_schema_version_readable(db_path: Path) -> None:
    """Raise :class:`FutureSchemaError` if ``db_path`` is newer than this build.

    Reads ``PRAGMA user_version`` on a throwaway connection WITHOUT going
    through ``open_index_database`` — that function's job is to migrate (or,
    for unrecognized versions, rebuild) the file it opens, which is exactly
    the destructive path this check exists to preempt.

    Raises ``sqlite3.DatabaseError`` naming ``db_path`` if the file is not a
    readable SQLite database (truncated/garbage bytes, a directory named
    ``*.db``, a permission-denied file, ...). ``sqlite3``'s own message
    ("file is not a database") never includes the path, which is unlocatable
    in a workspace of many bundles (see :func:`discover_workspace`) — wrap and
    re-raise with ``db_path`` attached so the operator can identify the
    offending bundle without bisecting the workspace by hand.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise sqlite3.DatabaseError(f"{db_path}: {exc}") from exc
    if version > SCHEMA_VERSION:
        raise FutureSchemaError(
            f"index database {db_path} was built with schema version {version}, "
            f"newer than this build supports (max {SCHEMA_VERSION}). Refusing to "
            "open it read-only to avoid the migration ladder's unrecognized-version "
            "rebuild wiping the bundle. Upgrade pydocs-mcp to a version that "
            f"supports schema {version}."
        )


@dataclass(frozen=True, slots=True)
class LoadedProject:
    """One indexed database selected for serving, with its routing identity.

    ``metadata`` is always present — a pre-v11 db gets a
    :meth:`IndexMetadata.legacy_fallback` synthesized from its
    ``packages.embedding_model`` (dim unknown, ``indexed_at=0.0``).
    """

    name: str
    db_path: Path
    metadata: IndexMetadata

    @property
    def indexed_at(self) -> float:
        """Recency for the most-recent-wins dedup tiebreak (0.0 if unstamped)."""
        return self.metadata.indexed_at


def _name_from_stem(stem: str) -> str:
    """Recover the project name from a ``{name}_{slug}`` db filename stem."""
    m = _SLUG_RE.match(stem)
    return m.group(1) if m else stem


def _legacy_embedding_model(conn) -> str | None:
    """The pre-v11 embedder identity: any non-empty ``packages.embedding_model``."""
    row = conn.execute(
        "SELECT embedding_model FROM packages "
        "WHERE embedding_model IS NOT NULL AND embedding_model != '' LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def load_project(db_path: Path) -> LoadedProject:
    """Open ``db_path``, read its stamped identity, and derive its project name.

    The routing name prefers the stamped ``index_metadata.project_name``; a legacy
    (unstamped) db falls back to a synthesized metadata (embedder from
    ``packages.embedding_model``) and the ``{name}_{slug}`` filename prefix.

    Raises ``FileNotFoundError`` if the db file is absent — loading a pre-built
    bundle that does not exist (or querying a never-indexed project) is an error,
    not an empty index (``open_index_database`` would otherwise create it).

    Raises :class:`FutureSchemaError` if the bundle's stamped schema version is
    newer than this build supports — opening it via ``open_index_database``
    would otherwise silently drop every table and re-serve it as an empty index.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"index database not found: {db_path}")
    _check_schema_version_readable(db_path)
    conn = open_index_database(db_path)
    try:
        meta = read_index_metadata(conn)
        if meta is None:
            meta = IndexMetadata.legacy_fallback(
                project_name=_name_from_stem(db_path.stem),
                embedding_model=_legacy_embedding_model(conn),
            )
    finally:
        conn.close()
    name = meta.project_name or _name_from_stem(db_path.stem)
    return LoadedProject(name=name, db_path=db_path, metadata=meta)


def discover_workspace(workspace: Path) -> list[LoadedProject]:
    """Load every ``*.db`` bundle directly under ``workspace`` (non-recursive).

    Raises ``FileNotFoundError`` if the directory is missing and ``ValueError`` if
    it holds no ``.db`` files — a silent empty load would look like "no results".
    """
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace directory not found: {workspace}")
    # Defense in depth (spec 2026-07-11 §3.1): the cross-link overlay sidecar
    # must NEVER be loaded as a project bundle — its canonical name doesn't
    # match *.db, but a decoy/rename (pydocs-links.db) must not regress this.
    dbs = sorted(db for db in workspace.glob("*.db") if not db.name.startswith("pydocs-links."))
    if not dbs:
        raise ValueError(f"no .db bundles found in workspace: {workspace}")
    return [load_project(db) for db in dbs]


class EmbedderMismatchError(RuntimeError):
    """A loaded db's vectors were built with an embedder the pipeline can't use."""


def validate_project_embedder(project: LoadedProject, *, model: str, dim: int) -> None:
    """Raise :class:`EmbedderMismatchError` if ``project`` is incompatible with the embedder.

    Multi-repo serving is READ-ONLY — an absent source can't be re-embedded — so a
    mismatch must fail fast here with a clear message rather than panic deep inside
    turbovec at query time on a dim mismatch. Legacy dbs that never recorded their
    embedder identity are permitted (they cannot be checked).
    """
    got = project.metadata
    if not got.embedder_matches(model=model, dim=dim):
        raise EmbedderMismatchError(
            f"project {project.name!r} ({project.db_path}) was indexed with embedder "
            f"{got.embedding_model!r} (dim {got.embedding_dim}), but the configured "
            f"pipeline uses {model!r} (dim {dim}). Re-index the project with the "
            f"matching embedder, or point the pipeline at {got.embedding_model!r}."
        )


def validate_project_embedders(projects: list[LoadedProject], *, model: str, dim: int) -> None:
    """Validate every loaded project against the configured embedder (fail-fast)."""
    for project in projects:
        validate_project_embedder(project, model=model, dim=dim)


def select_project(projects: list[LoadedProject], name: str) -> LoadedProject:
    """Pick the loaded project matching ``name``.

    A ``name`` may match more than one loaded db (two projects share a directory
    name at different paths). Per the multi-repo priority rule, the
    most-recently-indexed wins; pass a fuller ``{name}_{slug}`` filename stem to
    disambiguate precisely. Raises ``KeyError`` when nothing matches.
    """
    matches = [p for p in projects if p.name == name]
    if not matches:
        # Allow selecting by the full filename stem (disambiguation escape hatch).
        matches = [p for p in projects if p.db_path.stem == name]
    if not matches:
        available = ", ".join(sorted({p.name for p in projects}))
        raise KeyError(f"no loaded project named {name!r}; available: {available}")
    return max(matches, key=lambda p: p.indexed_at)
