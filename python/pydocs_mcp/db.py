"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 9  # v9: no structural change — forces a tree re-extraction so
# document_trees repopulate with the FULL multi-line ``extra_metadata["signature"]``
# header + decorator call args (``@app.route('/x')``), neither of which any
# content_hash covers. v8 forced the same re-extraction for pageindex decorators;
# v7 added ``chunks.qualified_name`` (tree-reasoning join key).

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT, embedding_model TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        module TEXT DEFAULT '',
        title TEXT, text TEXT, origin TEXT,
        content_hash TEXT,
        qualified_name TEXT
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package,
        content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE TABLE document_trees (
        package TEXT NOT NULL,
        module TEXT NOT NULL,
        tree_json TEXT NOT NULL,
        content_hash TEXT,
        updated_at REAL,
        PRIMARY KEY (package, module)
    );
    CREATE TABLE node_references (
        from_package   TEXT NOT NULL,
        from_node_id   TEXT NOT NULL,
        to_name        TEXT NOT NULL,
        to_node_id     TEXT,
        kind           TEXT NOT NULL,
        PRIMARY KEY (from_package, from_node_id, to_name, kind)
    );
    -- Note: ``PRAGMA foreign_keys`` is NOT enabled by ``open_index_database``,
    -- so the FK CASCADE below is declarative-only documentation today. Per-package
    -- cleanup is handled explicitly by ``remove_package`` / ``clear_all_packages``;
    -- the FastPlaid wiring tasks extend those paths to DELETE from
    -- ``chunk_multi_vector_ids`` alongside the existing per-table sweeps.
    CREATE TABLE chunk_multi_vector_ids (
        chunk_id      INTEGER PRIMARY KEY,
        plaid_doc_id  INTEGER NOT NULL UNIQUE,
        package       TEXT    NOT NULL,
        pipeline_hash TEXT    NOT NULL,
        FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_chunks_module          ON chunks(module);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
    CREATE INDEX idx_trees_package         ON document_trees(package);
    CREATE INDEX ix_refs_from              ON node_references(from_package, from_node_id);
    CREATE INDEX ix_refs_to_name           ON node_references(to_name);
    CREATE INDEX ix_refs_to_node           ON node_references(to_node_id);
    CREATE INDEX idx_cmv_plaid_doc_id      ON chunk_multi_vector_ids(plaid_doc_id);
    CREATE INDEX idx_cmv_package           ON chunk_multi_vector_ids(package);
"""

# Tables we know about — dropped on a version mismatch so earlier schemas
# (including the pre-v2 `symbols` table) are cleared before recreating.
_KNOWN_TABLES = (
    "chunks_fts",
    "chunks",
    "module_members",
    "packages",
    "symbols",
    "document_trees",
    "node_references",
    "chunk_multi_vector_ids",  # new in v6
)


def cache_path_for_project(project_dir: Path) -> Path:
    """Return the per-project SQLite cache file path under ``CACHE_DIR``.

    Each project gets its own ``.db`` file derived from its absolute path,
    so multiple projects never share state.
    """
    # md5 used as a fast non-cryptographic path-fingerprint to derive a short
    # per-project cache slug; usedforsecurity=False signals intent to ruff/bandit.
    slug = hashlib.md5(str(project_dir.resolve()).encode(), usedforsecurity=False).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def turboquant_path_for_project(project_dir: Path) -> Path:
    """Return the per-project TurboQuant ``.tq`` sidecar path under ``CACHE_DIR``.

    Mirrors :func:`cache_path_for_project`: same dir, same path-hash slug,
    ``.tq`` suffix instead of ``.db``. The two files live side-by-side so a
    ``--force`` cache clear deletes both (caller's responsibility).

    NOTE: production now derives the ``.tq`` path from the resolved ``.db``
    path via ``db_path.with_suffix(".tq")`` (``storage/search_backend.py``
    ``_sqlite_composite_factory`` + the ``__main__.py`` integrity sweep), not
    by calling this helper. The slug derivation here and ``with_suffix(".tq")``
    must agree on the same stem — if this helper's slug logic changes, keep the
    two paths in sync so the sidecar a re-index writes matches what retrieval
    reads. This helper remains for callers that derive the ``.tq`` path
    directly from a ``project_dir``.
    """
    # See `cache_path_for_project` — same non-cryptographic slug derivation.
    slug = hashlib.md5(str(project_dir.resolve()).encode(), usedforsecurity=False).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.tq"


def _drop_all_known_tables(connection: sqlite3.Connection) -> None:
    for tbl in _KNOWN_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {tbl}")


def _try_add_column(conn: sqlite3.Connection, table: str, column_ddl: str) -> None:
    """ALTER TABLE ADD COLUMN that tolerates the column already existing.

    Used by the idempotent v3 additions sweep. SQLite raises
    ``OperationalError`` with ``duplicate column name`` when the column is
    already present; we swallow that case so the sweep is safe to re-run.
    Any other ``OperationalError`` propagates (real schema damage).
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _apply_v3_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v3 shape.

    Adds the ``document_trees`` table, its index, and the new columns
    (``chunks.module``, ``chunks.content_hash``, ``packages.local_path``).
    Every operation tolerates pre-existing state — ``CREATE ... IF NOT
    EXISTS`` for the table/index, ``_try_add_column`` swallowing duplicate-
    column errors for ALTERs. Used both as the v2→v3 forward migration
    and as a v3-on-open repair sweep.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS document_trees ("
        "package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL, "
        "content_hash TEXT, updated_at REAL, PRIMARY KEY (package, module))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package)")
    _try_add_column(conn, "chunks", "module TEXT DEFAULT ''")
    _try_add_column(conn, "chunks", "content_hash TEXT")
    _try_add_column(conn, "packages", "local_path TEXT")
    # The fresh-DB DDL also creates ix_chunks_module on the new ``chunks.module``
    # column. v2->v3 in-place migration adds the column via _try_add_column but
    # previously omitted the index — queries that filter chunks by module on a
    # migrated DB hit a full table scan until the next fresh rebuild.
    conn.execute("CREATE INDEX IF NOT EXISTS ix_chunks_module ON chunks(module)")


def _apply_v4_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v4 shape.

    Mirrors :func:`_apply_v3_additions` — ``CREATE TABLE IF NOT EXISTS``
    + ``CREATE INDEX IF NOT EXISTS``; no destructive drops. Used both as
    the v3 → v4 forward migration AND as a v4-on-open repair sweep
    (drift recovery, AC #3).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_references ("
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, "
        "to_name TEXT NOT NULL, to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (from_package, from_node_id, to_name, kind))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_refs_from ON node_references(from_package, from_node_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)")


def _apply_v5_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v5 shape.

    Adds ``packages.embedding_model TEXT`` so the indexer can force re-embed
    when YAML's embedding.model_name changes (spec §3.2). Mirrors the v3/v4
    pattern — ``_try_add_column`` swallows duplicate-column errors so the
    sweep is safe to re-run as a v5-on-open repair (drift recovery).
    """
    _try_add_column(conn, "packages", "embedding_model TEXT")


def _apply_v6_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v6 shape.

    Adds ``chunk_multi_vector_ids`` (id-mapping between ``chunks.id`` and
    fast-plaid's auto-assigned ``plaid_doc_id``) plus its two indices. The
    table starts empty — rows are populated by the late-interaction indexing
    pipeline on next index. Mirrors the v3/v4 pattern: ``CREATE TABLE IF NOT
    EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` keeps the sweep safe to re-run
    as a v6-on-open drift-recovery pass.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunk_multi_vector_ids ("
        "chunk_id INTEGER PRIMARY KEY, "
        "plaid_doc_id INTEGER NOT NULL UNIQUE, "
        "package TEXT NOT NULL, "
        "pipeline_hash TEXT NOT NULL, "
        "FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cmv_plaid_doc_id ON chunk_multi_vector_ids(plaid_doc_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cmv_package ON chunk_multi_vector_ids(package)")


def _apply_v7_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply every additive change that makes up the v7 shape.

    Adds ``chunks.qualified_name TEXT`` — the dotted symbol path
    (``pkg.mod.Class.method``) that ``llm_tree_reasoning`` joins LLM-picked
    tree nodes against. It is produced at extraction (``tree_flatten``) and
    carried in ``Chunk.metadata`` but was previously dropped at the SQLite
    boundary, so tree retrieval matched nothing. Nullable: existing rows read
    back ``None`` until the next re-index repopulates them (it is NOT part of
    ``compute_chunk_content_hash``, so adding it forces no re-embed). Mirrors
    the v3/v4 pattern — ``_try_add_column`` swallows duplicate-column errors so
    the sweep is safe to re-run as a v7-on-open drift-recovery pass.
    """
    _try_add_column(conn, "chunks", "qualified_name TEXT")


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, migrating or rebuilding per user_version.

    - v9 already: re-run v3..v7 sweeps (additive, idempotent; drift recovery),
      data preserved.
    - v2 / v3 / v4 / v6 / v7 / v8 → v9: walk all forward (additive, idempotent)
      structure sweeps, then clear ``packages.content_hash`` so the next index
      re-extracts every package — repopulating ``document_trees`` with the FULL
      multi-line ``extra_metadata["signature"]`` header + decorator call args
      (``@app.route('/x')``), neither of which any content_hash covers (v8
      forced the same re-extraction for pageindex decorators). NON-DESTRUCTIVE:
      every row survives; chunks + the ``.tq`` / multi-vector sidecars stay in
      place (the chunk content_hash is unchanged, so the re-extract diff skips
      re-embedding), and the stale trees keep serving until re-extraction
      replaces them.
    - v5 / any other mismatch: drop every known table and recreate from current
      DDL (v5 was a deliberate wipe to force a fresh fast-plaid index build).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current == SCHEMA_VERSION:
        # v9 — re-run additive sweeps for drift recovery; data preserved.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        _apply_v5_additions(conn)
        _apply_v6_additions(conn)
        _apply_v7_additions(conn)
    elif current in (2, 3, 4, 6, 7, 8):
        # v2/v3/v4/v6/v7/v8 → v9 — walk every forward (additive, idempotent)
        # structure sweep first. Rerunning them repairs drift in legacy
        # under-stamped DBs (some v3-stamped DBs lack document_trees /
        # content_hash / local_path; v6 lacks chunks.qualified_name) before
        # stamping forward.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        _apply_v5_additions(conn)
        _apply_v6_additions(conn)
        _apply_v7_additions(conn)
        # v9 carries no structural change. The extraction enrichment added the
        # FULL multi-line extra_metadata["signature"] header + decorator call
        # args (@app.route('/x')) to the document_trees JSON blob, which neither
        # the chunk nor the node content_hash covers — so an unchanged-files
        # reindex would skip the package and never refresh its trees. Clearing
        # content_hash routes every package through the existing hash-skip →
        # re-extract path on the next index (rewriting trees WITH the richer
        # metadata), while the chunk content_hash stays the same, so the diff
        # keeps unchanged chunks + their vectors in place (no re-embed).
        # Non-destructive: rows survive and stale trees keep serving until
        # re-extraction replaces them. Mirrors check_integrity_and_repair.
        conn.execute("UPDATE packages SET content_hash = NULL")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    else:
        _drop_all_known_tables(conn)
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all rows for a package across chunks, members, trees, refs, packages.

    The reference-graph capture (``node_references``) participates in the
    per-package sweep — without this, stale refs survive a re-index and
    ``ref_svc.callers(...)`` returns references to deleted source nodes.
    """
    connection.execute("DELETE FROM chunks  WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM document_trees WHERE package=?", (package_name,))
    connection.execute("DELETE FROM node_references WHERE from_package=?", (package_name,))
    connection.execute("DELETE FROM packages WHERE name=?", (package_name,))


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every indexed package across all five entity tables."""
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
    connection.execute("DELETE FROM document_trees")
    connection.execute("DELETE FROM node_references")
    connection.commit()


def rebuild_fulltext_index(connection: sqlite3.Connection) -> None:
    """Rebuild the FTS5 index after bulk writes so new rows become searchable."""
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    connection.commit()


def get_stored_content_hash(connection: sqlite3.Connection, package_name: str) -> str | None:
    """Return the stored content hash for a package, or ``None`` if not indexed."""
    row = connection.execute(
        "SELECT content_hash FROM packages WHERE name=?", (package_name,)
    ).fetchone()
    return row["content_hash"] if row else None


from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider  # noqa: E402


def build_connection_provider(cache_path: Path):
    """Factory — returns the default ConnectionProvider for a given DB path."""
    return PerCallConnectionProvider(cache_path=cache_path)
