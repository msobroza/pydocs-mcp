"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("pydocs-mcp")

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 14  # v14: additive — decision_records table (mined
# architectural decisions, spec §D8-§D10) + chunks.decision_id
# (searchable-projection backlink) + index_metadata.{activity_summary,
# overview_summary} (JSON aggregates for the overview card, §D17). NULL/empty
# until the next index; NO re-extraction or re-embed.
# v13: additive — index_metadata.git_head (the project's
# git HEAD sha stamped at index time; the freshness envelope compares it to
# the live .git/HEAD to emit a stale warning). Nullable: legacy rows read
# back None until the next index stamps it. NO re-extraction or re-embed.
# v12: additive — chunks.embedded flag (1 = a single-vector
# was written to the .tq for this chunk). Lets the integrity check compare
# INTENDED embeddings vs vectors, so selective embed policies (dependency doc
# pages only) don't read as drift and trigger the clear-all-content_hash loop.
# The upgrade backfills embedded=1 on existing rows (they were written under
# the embed-everything policy); no re-extraction forced.
# v11: additive — index_metadata table (single row: project
# identity + embedder identity + pipeline_hash + indexed_at) so a loader can
# reject a mismatched-embedder .tq and multi-repo search can route/dedup by
# project name and recency. Empty until the next index stamps it; no re-extract.
# v10: additive — node_scores table (in-degree / PageRank /
# community per node, computed at index time for the graph rerank steps). Purely
# additive: empty until the next index populates it; no re-extraction forced.
# v9: no structural change — forces a tree re-extraction so
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
        qualified_name TEXT,
        embedded INTEGER NOT NULL DEFAULT 0,
        decision_id INTEGER
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
    CREATE TABLE node_scores (
        package        TEXT    NOT NULL,
        qualified_name TEXT    NOT NULL,
        in_degree      INTEGER NOT NULL DEFAULT 0,
        pagerank       REAL    NOT NULL DEFAULT 0.0,
        community      INTEGER NOT NULL DEFAULT -1,
        PRIMARY KEY (package, qualified_name)
    );
    CREATE TABLE decision_records (
        id              INTEGER PRIMARY KEY,
        package         TEXT NOT NULL,
        title           TEXT NOT NULL,
        status          TEXT NOT NULL,
        source          TEXT NOT NULL,
        confidence      REAL NOT NULL,
        evidence        TEXT NOT NULL,
        affected_files  TEXT NOT NULL,
        affected_qnames TEXT NOT NULL,
        staleness_score REAL NOT NULL DEFAULT 0.0,
        superseded_by   INTEGER,
        verification    TEXT NOT NULL DEFAULT 'verbatim',
        structured      TEXT,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL
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
    CREATE INDEX ix_node_scores_qname      ON node_scores(qualified_name);
    CREATE INDEX ix_node_scores_package    ON node_scores(package);
    CREATE INDEX ix_decisions_package      ON decision_records(package);
    CREATE TABLE index_metadata (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        project_name TEXT, project_root TEXT,
        embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER,
        pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
        activity_summary TEXT, overview_summary TEXT
    );
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
    "node_scores",  # new in v10
    "index_metadata",  # new in v11
    "decision_records",  # new in v14
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


def _apply_v10_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v10 shape — the ``node_scores`` table + indices.

    Holds per-node graph signals (in-degree / PageRank / community) computed at
    index time for the centrality-prior and community-diversity rerank steps.
    Purely additive: the table starts empty and is repopulated by the next
    index's node-score recompute, so the migration forces NO re-extraction or
    re-embed (unlike v9). ``CREATE ... IF NOT EXISTS`` keeps the sweep safe to
    re-run as a v10-on-open drift-recovery pass.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_scores ("
        "package TEXT NOT NULL, "
        "qualified_name TEXT NOT NULL, "
        "in_degree INTEGER NOT NULL DEFAULT 0, "
        "pagerank REAL NOT NULL DEFAULT 0.0, "
        "community INTEGER NOT NULL DEFAULT -1, "
        "PRIMARY KEY (package, qualified_name))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_node_scores_qname ON node_scores(qualified_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_node_scores_package ON node_scores(package)")


def _apply_v11_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v11 shape — the single-row ``index_metadata`` table.

    Records the database's project identity + embedder identity + ``indexed_at``
    (see :mod:`pydocs_mcp.storage.index_metadata`). Purely additive: the table
    starts empty (legacy dbs read back no row and fall back to
    ``packages.embedding_model``) and is stamped by the next index, so the
    migration forces NO re-extraction or re-embed. ``CREATE ... IF NOT EXISTS``
    keeps the sweep safe to re-run as a v11-on-open drift-recovery pass.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS index_metadata ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "project_name TEXT, project_root TEXT, "
        "embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER, "
        "pipeline_hash TEXT, indexed_at REAL)"
    )


def _apply_v12_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v12 shape — the ``chunks.embedded`` flag column.

    ``embedded = 1`` records that a single-vector was written to the ``.tq``
    sidecar for this chunk (stamped by the vector-write path). The integrity
    check compares vectors against INTENDED embeddings (``WHERE embedded = 1``)
    instead of every chunk, so selective embed policies (dependency doc pages
    only, ``dependency_policy: none`` ...) are steady states, not drift.
    Backfill for pre-v12 rows happens in the UPGRADE branch only — re-running
    this sweep on-open must not overwrite flags written under a selective
    policy.
    """
    _try_add_column(conn, "chunks", "embedded INTEGER NOT NULL DEFAULT 0")


def _apply_v13_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v13 shape — ``index_metadata.git_head``.

    Stamped by the next index pass; NULL until then (the envelope renders
    age-only for a NULL head). ``_try_add_column`` swallows duplicate-column
    errors so the sweep is safe to re-run as a v13-on-open drift-recovery pass.
    """
    _try_add_column(conn, "index_metadata", "git_head TEXT")


def _apply_v14_additions(conn: sqlite3.Connection) -> None:
    """Idempotently apply the v14 shape — the decision layer (spec §D8-§D10, §D17).

    Adds ``decision_records`` (mined architectural decisions) + its package
    index, ``chunks.decision_id`` (the searchable-projection backlink from a
    decision chunk to its record), and the two ``index_metadata`` JSON aggregate
    columns (``activity_summary`` / ``overview_summary``) that feed the overview
    card. Purely additive: the table starts empty, the columns read back NULL
    until the next index stamps them, so the migration forces NO re-extraction
    or re-embed. ``CREATE ... IF NOT EXISTS`` + ``_try_add_column`` keep the
    sweep safe to re-run as a v14-on-open drift-recovery pass.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS decision_records ("
        "id INTEGER PRIMARY KEY, "
        "package TEXT NOT NULL, "
        "title TEXT NOT NULL, "
        "status TEXT NOT NULL, "
        "source TEXT NOT NULL, "
        "confidence REAL NOT NULL, "
        "evidence TEXT NOT NULL, "
        "affected_files TEXT NOT NULL, "
        "affected_qnames TEXT NOT NULL, "
        "staleness_score REAL NOT NULL DEFAULT 0.0, "
        "superseded_by INTEGER, "
        "verification TEXT NOT NULL DEFAULT 'verbatim', "
        "structured TEXT, "
        "created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_decisions_package ON decision_records(package)")
    _try_add_column(conn, "chunks", "decision_id INTEGER")
    _try_add_column(conn, "index_metadata", "activity_summary TEXT")
    _try_add_column(conn, "index_metadata", "overview_summary TEXT")


def _migrate_in_place(conn: sqlite3.Connection, current: int) -> None:
    """Run the version-appropriate additive sweeps and stamp ``user_version``.

    Raises ``sqlite3.OperationalError`` when the DB has drifted beyond what
    the idempotent sweeps can heal (e.g. a missing CORE table like ``chunks``
    makes ``_try_add_column`` fail with "no such table") — the caller falls
    back to a full rebuild so the open NEVER crash-loops.
    """
    if current == SCHEMA_VERSION:
        # v14 — re-run additive sweeps for drift recovery; data preserved.
        # (No embedded-flag backfill here: flags written under a selective
        # embed policy must survive reopen.)
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        _apply_v5_additions(conn)
        _apply_v6_additions(conn)
        _apply_v7_additions(conn)
        _apply_v10_additions(conn)
        _apply_v11_additions(conn)
        _apply_v12_additions(conn)
        _apply_v13_additions(conn)
        _apply_v14_additions(conn)
    elif current in (12, 13):
        # v12/v13 → v14 — additive git_head (v13, no-op on v13 DBs) + the
        # decision layer (v14). The FULL sweep chain runs (not just the tail):
        # each sweep is idempotent, and the early ones heal structural drift
        # in place — a v13-stamped DB missing ``index_metadata`` gets the
        # table recreated by the v11 sweep instead of crash-looping on the
        # v13/v14 ALTERs ("no such table" raised before the version stamp,
        # so every subsequent open died identically). NO embedded backfill:
        # v12/v13 flags may have been written under a selective embed policy.
        _apply_v3_additions(conn)
        _apply_v4_additions(conn)
        _apply_v5_additions(conn)
        _apply_v6_additions(conn)
        _apply_v7_additions(conn)
        _apply_v10_additions(conn)
        _apply_v11_additions(conn)
        _apply_v12_additions(conn)
        _apply_v13_additions(conn)
        _apply_v14_additions(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current in (9, 10, 11):
        # v9/v10/v11 → v12 — purely additive: create node_scores (v10) +
        # index_metadata (v11) + chunks.embedded (v12). Pre-v12 rows were
        # written under the embed-everything policy, so backfill embedded=1 —
        # their vectors ARE in the .tq (SQLite-only deployments with no
        # vectors converge after one repair pass instead of looping forever).
        # NO content_hash clear / re-extraction needed.
        _apply_v10_additions(conn)
        _apply_v11_additions(conn)
        _apply_v12_additions(conn)
        _apply_v13_additions(conn)
        _apply_v14_additions(conn)
        conn.execute("UPDATE chunks SET embedded = 1")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
        _apply_v10_additions(conn)
        _apply_v11_additions(conn)
        _apply_v12_additions(conn)
        _apply_v13_additions(conn)
        _apply_v14_additions(conn)
        conn.execute("UPDATE chunks SET embedded = 1")
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
        _rebuild_from_scratch(conn)


def _rebuild_from_scratch(conn: sqlite3.Connection) -> None:
    _drop_all_known_tables(conn)
    conn.executescript(_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, migrating or rebuilding per user_version.

    - v9 already: re-run v3..v7 sweeps (additive, idempotent; drift recovery),
      data preserved.
    - v12 / v13 → v14: the full additive sweep chain (idempotent), so
      structural drift in older tables is healed in place; data preserved,
      NO ``embedded`` backfill (selective-policy flags survive).
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
    - drift the sweeps cannot heal (a missing CORE table makes an ALTER raise
      "no such table"): rebuild from scratch instead of letting the error
      escape. The error used to propagate BEFORE the version stamp, so every
      subsequent open re-took the same branch and crashed identically — a
      permanent crash-loop only fixable by manually deleting the ``.db``. The
      cache is derived data; an empty working DB beats a bricked one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    try:
        _migrate_in_place(conn, current)
    except sqlite3.OperationalError as exc:
        log.warning(
            "in-place migration from user_version=%s failed (%s) — "
            "rebuilding the index cache from scratch; the next `pydocs-mcp "
            "index` run repopulates it",
            current,
            exc,
        )
        conn.rollback()
        _rebuild_from_scratch(conn)
    conn.commit()
    return conn


def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all rows for a package across chunks, members, trees, refs, packages.

    The reference-graph capture (``node_references``) participates in the
    per-package sweep — without this, stale refs survive a re-index and
    ``ref_svc.callers(...)`` returns references to deleted source nodes.

    ``chunks_fts`` is external-content (``content=chunks``): it does NOT
    observe the ``DELETE FROM chunks`` below, so the index must be synced
    first via FTS5's ``'delete'`` command (which needs the old column
    values, hence the SELECT). Skipping this left orphaned index entries:
    SQLite reuses the freed rowids (no AUTOINCREMENT), so old-token queries
    silently matched the NEW chunk occupying the rowid, and FTS5's
    ``integrity-check`` reported the db as malformed.
    """
    stale = connection.execute(
        "SELECT id, title, text, package FROM chunks WHERE package=?",
        (package_name,),
    ).fetchall()
    try:
        connection.executemany(
            "INSERT INTO chunks_fts(chunks_fts, rowid, title, text, package) "
            "VALUES('delete', ?, ?, ?, ?)",
            [(row[0], row[1], row[2], row[3]) for row in stale],
        )
        fts_synced = True
    except sqlite3.DatabaseError:
        # The 'delete' command requires the index to hold EXACTLY these
        # values; rows inserted but never indexed (rebuild runs at the end
        # of a full pass) or prior unsynced deletes make it raise
        # "malformed". Fall back to a full rebuild AFTER the content
        # deletes below — O(corpus) instead of O(package), but it also
        # heals whatever pre-existing drift caused the mismatch.
        fts_synced = False
    connection.execute("DELETE FROM chunks  WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM document_trees WHERE package=?", (package_name,))
    connection.execute("DELETE FROM node_references WHERE from_package=?", (package_name,))
    connection.execute("DELETE FROM node_scores WHERE package=?", (package_name,))
    connection.execute("DELETE FROM packages WHERE name=?", (package_name,))
    if not fts_synced:
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every indexed package across all five entity tables.

    ``'delete-all'`` empties the external-content FTS index in the same
    transaction — see :func:`remove_package` for why the index must not
    be left pointing at deleted content rows.
    """
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
    connection.execute("DELETE FROM document_trees")
    connection.execute("DELETE FROM node_references")
    connection.execute("DELETE FROM node_scores")
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
