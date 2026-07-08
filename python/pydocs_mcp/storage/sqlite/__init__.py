"""SQLite storage adapters — one repository per module (CLAUDE.md §SRP).

Package façade over the former ``storage/sqlite.py`` monolith: re-exports
the full public + test-visible surface so every existing
``from pydocs_mcp.storage.sqlite import X`` site works unchanged.
Underscore names are re-exported deliberately — tests and the
composition-root factories consume them, and mirroring the old module
namespace exactly is what makes the split behavior-invisible. New code
inside ``storage/`` should import shared internals from their owning
submodules (``transaction``, ``filter_adapter``, ``row_mappers``) instead.
"""

from __future__ import annotations

# ``_build_fts_match_query`` is re-exported for compatibility with the
# pre-PR-D module namespace; the canonical home is ``storage/fts_query``.
from pydocs_mcp.storage.fts_query import build_fts_match_query as _build_fts_match_query
from pydocs_mcp.storage.sqlite.chunk_multi_vector_repository import (
    SqliteChunkMultiVectorRepository,
)
from pydocs_mcp.storage.sqlite.chunk_repository import SqliteChunkRepository
from pydocs_mcp.storage.sqlite.decision_repository import (
    SqliteDecisionRepository,
    _row_to_decision_record,
)
from pydocs_mcp.storage.sqlite.document_tree_store import (
    SqliteDocumentTreeStore,
    _deserialize_tree_from_json,
    _dict_to_node,
    _node_to_dict,
    _serialize_tree_to_json,
)
from pydocs_mcp.storage.sqlite.filter_adapter import (
    _MEMBER_COLUMNS,
    _PACKAGE_COLUMNS,
    CHUNK_COLUMNS,
    SqliteFilterAdapter,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.fts_store import SqliteLexicalStore
from pydocs_mcp.storage.sqlite.module_member_repository import SqliteModuleMemberRepository
from pydocs_mcp.storage.sqlite.node_score_repository import (
    SqliteNodeScoreRepository,
    _row_to_node_score,
)
from pydocs_mcp.storage.sqlite.package_repository import SqlitePackageRepository
from pydocs_mcp.storage.sqlite.reference_store import (
    SqliteReferenceStore,
    _row_to_node_reference,
)
from pydocs_mcp.storage.sqlite.row_mappers import (
    _chunk_to_row,
    _module_member_to_row,
    _package_to_row,
    _row_to_module_member,
    _row_to_package,
    row_to_chunk,
)
from pydocs_mcp.storage.sqlite.table_crud import _resolve_filter
from pydocs_mcp.storage.sqlite.transaction import _maybe_acquire, _sqlite_transaction
from pydocs_mcp.storage.sqlite.uow import SqliteUnitOfWork

# Explicit __all__ (including underscore names) is what satisfies ruff F401
# for re-export-only imports AND pins the exact pre-split module surface.
__all__ = [
    "CHUNK_COLUMNS",
    "_MEMBER_COLUMNS",
    "_PACKAGE_COLUMNS",
    "SqliteChunkMultiVectorRepository",
    "SqliteChunkRepository",
    "SqliteDecisionRepository",
    "SqliteDocumentTreeStore",
    "SqliteFilterAdapter",
    "SqliteLexicalStore",
    "SqliteModuleMemberRepository",
    "SqliteNodeScoreRepository",
    "SqlitePackageRepository",
    "SqliteReferenceStore",
    "SqliteUnitOfWork",
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
    "_row_to_decision_record",
    "_row_to_module_member",
    "_row_to_node_reference",
    "_row_to_node_score",
    "_row_to_package",
    "_serialize_tree_to_json",
    "_sqlite_transaction",
    "row_to_chunk",
]
