"""Schema v18 — the branch key on the tree tier (spec §6.1 v18, multi-branch P1).

Lives beside ``db.py`` rather than inside it because ``db.py`` is already far
past the repository's file-size rule; ``db.py`` keeps the ladder (the version
arms and the additive-sweep table) and calls the two entry points here:

- :func:`rebuild_tree_tier_with_branch_key` — the three tables whose primary key
  gains a leading ``branch`` column. SQLite cannot alter a primary key, so each
  is rebuilt by copy, rowids kept.
- :func:`stamp_project_rows_with_default_branch` — the v17 → v18 data step: the
  project's tree-tier rows take the default branch's name; dependency rows keep
  ``''`` forever (the branch dimension is project-only, spec Q1).

Imports nothing from the package: ``db.py`` imports this module at load time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

BRANCH_COLUMN_DDL = "branch TEXT NOT NULL DEFAULT ''"


@dataclass(frozen=True, slots=True)
class BranchKeyRebuild:
    """One table whose primary key gains ``branch``.

    ``columns`` lists every pre-v18 column (the copy source), ``indexes`` the
    table's secondary indexes: dropping the renamed original drops them, so
    they are recreated on the new table under the same names.
    """

    table: str
    ddl: str
    columns: str
    indexes: tuple[str, ...]


# The v18 shape of the three rebuilt tables. db.py's fresh ``_DDL`` spells the
# same tables; tests/test_db_schema_v18_migration.py pins a migrated bundle's
# shape to a fresh one's, so the two cannot drift apart silently.
TREE_TIER_KEY_REBUILDS: tuple[BranchKeyRebuild, ...] = (
    BranchKeyRebuild(
        table="document_trees",
        ddl="CREATE TABLE document_trees (branch TEXT NOT NULL DEFAULT '', "
        "package TEXT NOT NULL, module TEXT NOT NULL, tree_json TEXT NOT NULL, "
        "content_hash TEXT, updated_at REAL, PRIMARY KEY (branch, package, module))",
        columns="package, module, tree_json, content_hash, updated_at",
        indexes=(
            "CREATE INDEX IF NOT EXISTS idx_trees_package ON document_trees(package)",
            # SqliteDocumentTreeStore.load / exists seek (package, module): their
            # branch predicate is written ``+branch IN (...)`` on purpose (#307),
            # so the branch-led key never serves the probe — without this index
            # each one scans the package's whole range.
            "CREATE INDEX IF NOT EXISTS idx_trees_package_module "
            "ON document_trees(package, module)",
        ),
    ),
    BranchKeyRebuild(
        table="node_references",
        ddl="CREATE TABLE node_references (branch TEXT NOT NULL DEFAULT '', "
        "from_package TEXT NOT NULL, from_node_id TEXT NOT NULL, to_name TEXT NOT NULL, "
        "to_node_id TEXT, kind TEXT NOT NULL, "
        "PRIMARY KEY (branch, from_package, from_node_id, to_name, kind))",
        columns="from_package, from_node_id, to_name, to_node_id, kind",
        indexes=(
            "CREATE INDEX IF NOT EXISTS ix_refs_from "
            "ON node_references(from_package, from_node_id)",
            "CREATE INDEX IF NOT EXISTS ix_refs_to_name ON node_references(to_name)",
            "CREATE INDEX IF NOT EXISTS ix_refs_to_node ON node_references(to_node_id)",
        ),
    ),
    BranchKeyRebuild(
        table="node_scores",
        ddl="CREATE TABLE node_scores (branch TEXT NOT NULL DEFAULT '', "
        "package TEXT NOT NULL, qualified_name TEXT NOT NULL, "
        "in_degree INTEGER NOT NULL DEFAULT 0, pagerank REAL NOT NULL DEFAULT 0.0, "
        "community INTEGER NOT NULL DEFAULT -1, "
        "PRIMARY KEY (branch, package, qualified_name))",
        columns="package, qualified_name, in_degree, pagerank, community",
        indexes=(
            "CREATE INDEX IF NOT EXISTS ix_node_scores_qname ON node_scores(qualified_name)",
            "CREATE INDEX IF NOT EXISTS ix_node_scores_package ON node_scores(package)",
        ),
    ),
)

# The five branch-keyed tables and the column naming each row's package.
_BRANCH_KEYED_TABLES = (
    ("document_trees", "package"),
    ("module_members", "package"),
    ("node_references", "from_package"),
    ("node_scores", "package"),
    ("decision_records", "package"),
)

# The ONE "which branch does the bundle serve" rule: the ``branches`` row stamped
# ``is_default``, newest stamp first. The stamp below names project rows after it
# and every ``branch=None`` tree-tier read resolves through it
# (``storage/sqlite/table_crud.branch_read_clause``), as do
# ``SqliteBranchRepository.default_branch_name`` and the freshness probe — two
# copies that drifted apart would hide every project row from every tool (#307).
# Defined here because this module imports nothing from the package, so storage
# can import it without a cycle through ``db.py``.
DEFAULT_BRANCH_NAME_SQL = (
    "SELECT name FROM branches WHERE is_default = 1 ORDER BY indexed_at DESC LIMIT 1"
)
_REBUILD_SIDE_SUFFIX = "__pre_v18"


def rebuild_tree_tier_with_branch_key(conn: sqlite3.Connection) -> None:
    """Give each tree-tier table its v18 primary key; a no-op once done."""
    for rebuild in TREE_TIER_KEY_REBUILDS:
        _rebuild_keyed_by_branch(conn, rebuild)


def _rebuild_keyed_by_branch(conn: sqlite3.Connection, rebuild: BranchKeyRebuild) -> None:
    columns = _column_names(conn, rebuild.table)
    if "branch" in columns:
        # Already keyed: a repair only restores an index gone missing.
        _create_indexes(conn, rebuild)
        return
    if not columns:
        # Drift: the table is gone (the v9..v11 arm replays only the sweeps
        # newer than v9, so nothing else recreates it). Create it in the v18
        # shape rather than fail the rename — a failed open rebuilds from scratch.
        conn.execute(rebuild.ddl)
        _create_indexes(conn, rebuild)
        return
    _copy_into_branch_keyed_table(conn, rebuild)


def _copy_into_branch_keyed_table(conn: sqlite3.Connection, rebuild: BranchKeyRebuild) -> None:
    side = f"{rebuild.table}{_REBUILD_SIDE_SUFFIX}"
    conn.execute(f"ALTER TABLE {rebuild.table} RENAME TO {side}")
    conn.execute(rebuild.ddl)
    # rowid carried over explicitly: readers without an ORDER BY return rows in
    # index-then-rowid order, so a renumbered copy could reorder an answer.
    # Every interpolated name is a module constant (TREE_TIER_KEY_REBUILDS).
    copy_rows = (
        f"INSERT INTO {rebuild.table} (rowid, {rebuild.columns}) "  # noqa: S608
        f"SELECT rowid, {rebuild.columns} FROM {side} ORDER BY rowid"
    )
    conn.execute(copy_rows)
    # Index names are global and the renamed table still holds the old ones:
    # recreated before this DROP, ``IF NOT EXISTS`` would skip them and the
    # DROP would then take them away for good.
    conn.execute(f"DROP TABLE {side}")
    _create_indexes(conn, rebuild)


def _create_indexes(conn: sqlite3.Connection, rebuild: BranchKeyRebuild) -> None:
    for statement in rebuild.indexes:
        conn.execute(statement)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """``table``'s columns; empty when the table does not exist."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def stamp_project_rows_with_default_branch(conn: sqlite3.Connection, project_package: str) -> None:
    """v17 → v18: rows written before the branch key belong to the default branch.

    A bundle with no default branch (never indexed under v16+, or a v12..v15
    bundle whose project hash the same step clears) keeps ``''`` until the next
    pass rewrites its rows. Only unstamped rows are touched, so a replay is inert.
    """
    row = conn.execute(DEFAULT_BRANCH_NAME_SQL).fetchone()
    if row is None:
        return
    for table, package_column in _BRANCH_KEYED_TABLES:
        # Table and column come from _BRANCH_KEYED_TABLES; values bind.
        stamp = f"UPDATE {table} SET branch = ? WHERE {package_column} = ? AND branch = ''"  # noqa: S608
        conn.execute(stamp, (row[0], project_package))
