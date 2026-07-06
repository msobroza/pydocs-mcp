"""Package-layout guarantees for the storage/sqlite split.

The ``storage/sqlite.py`` monolith became the ``storage/sqlite/``
package. These tests pin the invariants the split must not break:
(1) every name of the old module namespace — including the underscore
names tests and the composition-root factories consume — still
resolves from ``pydocs_mcp.storage.sqlite``; (2) re-exports are the
SAME objects as the submodule originals (ContextVar identity is what
keeps the ambient-transaction plumbing working across the split).
"""

from __future__ import annotations

import pydocs_mcp.storage.sqlite as sqlite_pkg

# Exact mirror of the pre-split module namespace (post-PR-D: FTS builder
# was already extracted to storage/fts_query, so _FTS_OPS / _FTS_SAFE_TOKEN
# are absent; SqliteLexicalStore is the canonical class name for the FTS5
# text-search view with SqliteVectorStore kept as a deprecated alias).
_EXPECTED_SURFACE = (
    "CHUNK_COLUMNS",
    "SqliteChunkMultiVectorRepository",
    "SqliteChunkRepository",
    "SqliteDocumentTreeStore",
    "SqliteFilterAdapter",
    "SqliteLexicalStore",
    "SqliteModuleMemberRepository",
    "SqliteNodeScoreRepository",
    "SqlitePackageRepository",
    "SqliteReferenceStore",
    "SqliteUnitOfWork",
    "SqliteVectorStore",
    "_MEMBER_COLUMNS",
    "_PACKAGE_COLUMNS",
    "_SqliteFilterTranslator",
    "_build_fts_match_query",
    "_chunk_to_row",
    "_deserialize_tree_from_json",
    "_dict_to_node",
    "_maybe_acquire",
    "_module_member_to_row",
    "_node_to_dict",
    "_package_to_row",
    "_resolve_filter",
    "_row_to_module_member",
    "_row_to_node_reference",
    "_row_to_node_score",
    "_row_to_package",
    "_serialize_tree_to_json",
    "_sqlite_transaction",
    "row_to_chunk",
)


def test_sqlite_is_a_package() -> None:
    # A package has __path__; a plain module does not. This is the split's
    # existence check — it fails before Task 1's git mv and passes after.
    assert hasattr(sqlite_pkg, "__path__")


def test_package_mirrors_the_old_module_namespace() -> None:
    missing = [name for name in _EXPECTED_SURFACE if not hasattr(sqlite_pkg, name)]
    assert missing == []


def test_transaction_module_owns_the_ambient_contextvar() -> None:
    from pydocs_mcp.storage.sqlite import transaction

    # Identity, not equality: SqliteUnitOfWork sets this ContextVar and
    # _maybe_acquire reads it — two ContextVar objects would silently
    # break ambient-transaction reuse.
    assert sqlite_pkg._sqlite_transaction is transaction._sqlite_transaction
    assert sqlite_pkg._maybe_acquire is transaction._maybe_acquire


def test_row_mappers_module_owns_the_mappers() -> None:
    from pydocs_mcp.storage.sqlite import row_mappers

    assert sqlite_pkg.row_to_chunk is row_mappers.row_to_chunk
    assert sqlite_pkg._chunk_to_row is row_mappers._chunk_to_row
    assert sqlite_pkg._row_to_package is row_mappers._row_to_package
