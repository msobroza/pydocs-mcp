"""MemberFetcherStep — candidate generation via SQLite LIKE on ``module_members``.

Single responsibility: take a query, return up to N candidate module
members whose ``name`` or ``docstring`` contains the query terms
(case-insensitive substring match). No score normalization, no top-K
cutoff, no rendering — LIKE doesn't produce relevance ranks, so
candidates carry ``relevance=None`` and downstream
:class:`TopKFilterStep` handles the cap with source-order fallback.

Pre-filter pushdown (Task 8): when ``state.query.pre_filter`` is set,
the filter tree is parsed through the configured
``MetadataFilterFormat``, validated against the schema's allowed
fields, and pushed into the SQL ``WHERE`` clause through the same
:class:`SqliteFilterAdapter` that ``SqliteModuleMemberRepository.list``
uses — so AC17 SQL parity is preserved. The semantic ``scope`` field
is split out via ``_split_scope`` and re-applied in-process via
``_matches_scope`` (the SQL adapter rejects ``scope`` as an unsafe
column, mirroring the legacy ``LikeMemberRetriever`` flow).

Mirrors the LIKE query shape the legacy ``LikeMemberRetriever`` used
(deleted in Task 9) but pushes the substring match down to SQL instead
of post-filtering in Python.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import (
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    Parameter,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

# Deferred storage / filter_helpers imports — see
# :mod:`pydocs_mcp.retrieval.steps.chunk_fetcher` for the rationale.
# Importing inside ``run`` breaks the storage→extraction→
# retrieval.config→retrieval.steps cycle at module-load time.


# Legacy LIKE-based query — Task 4 split fetcher from filter, Task 8
# folds pre-filter pushdown into the fetcher (mirroring
# ChunkFetcherStep). NOTE: legacy ``LikeMemberRetriever`` fetched WITHOUT
# the LIKE constraint (relying on Python substring post-filter), so
# parity requires the same flow here — the SQL pre-filter only carries
# the metadata filter (package/module/name/kind), and the LIKE pass
# happens post-fetch via ``_keep_by_terms``.
_FETCH_SQL_TEMPLATE = (
    "SELECT id, package, module, name, kind, signature, return_annotation, "
    "parameters, docstring "
    "FROM module_members "
    "{where_clause}"
    "LIMIT ?"
)

# WHY: single source of truth for the member-fetch defaults. Referenced
# from the dataclass field defaults + to_dict (omit-when-default) +
# from_dict (fallback when YAML omits the key).
_DEFAULT_LIMIT = 50
_DEFAULT_RETRIEVER_NAME = "like_member"


@step_registry.register("member_fetcher")
@dataclass(frozen=True, slots=True)
class MemberFetcherStep(RetrieverStep):
    """Candidate generation step for member pipelines.

    Reads ``state.query.terms`` (LIKE) and ``state.query.pre_filter``
    (SQL pushdown). Writes ``state.candidates`` as a
    :class:`ModuleMemberList`. Each candidate's ``relevance`` is ``None``
    — LIKE produces no rank. :class:`TopKFilterStep` downstream handles
    the "no relevance" case via source-order fallback.
    """

    provider: ConnectionProvider
    allowed_fields: frozenset[str] = field(default=frozenset(), kw_only=True)
    limit: int = field(default=_DEFAULT_LIMIT, kw_only=True)
    retriever_name: str = field(default=_DEFAULT_RETRIEVER_NAME, kw_only=True)
    name: str = field(default="member_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        needle = state.query.terms.strip().lower()
        if not needle:
            return replace(state, candidates=ModuleMemberList(items=()))

        # Lazy imports — see module docstring on the import cycle.
        from pydocs_mcp.retrieval.filter_helpers import (
            _matches_scope,
            _schema_from_fields,
            _split_scope,
        )
        from pydocs_mcp.storage.filters import format_registry

        # Pre-filter pushdown — same shape as ChunkFetcherStep.
        tree = None
        scope: frozenset[SearchScope] | None = None
        if state.query.pre_filter is not None:
            tree = format_registry[state.query.pre_filter_format].parse(state.query.pre_filter)
            _schema_from_fields(self.allowed_fields).validate(tree)
            tree, scope = _split_scope(tree)

        filter_sql = ""
        filter_params: list = []
        if tree is not None:
            from pydocs_mcp.storage.sqlite import (
                _MEMBER_COLUMNS,
                SqliteFilterAdapter,
            )
            adapter = SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
            filter_sql, filter_params = adapter.adapt(tree)

        rows = await asyncio.to_thread(
            self._fetch_sync, filter_sql, filter_params,
        )
        members = tuple(_row_to_candidate(row, self.retriever_name) for row in rows)
        # Apply LIKE-style substring match in-process (matches legacy
        # LikeMemberRetriever's Python-side `needle in name or needle in
        # docstring` post-filter).
        members = tuple(_keep_by_terms(m, needle) for m in members)
        members = tuple(m for m in members if m is not None)
        if scope is not None:
            members = tuple(
                m for m in members
                if _matches_scope(
                    str(m.metadata.get(ModuleMemberFilterField.PACKAGE.value, "")),
                    scope,
                )
            )
        return replace(state, candidates=ModuleMemberList(items=members))

    def _fetch_sync(
        self, filter_sql: str, filter_params: list,
    ) -> list[sqlite3.Row]:
        # WHY: PerCallConnectionProvider exposes ``cache_path`` directly so a
        # sync-friendly fresh connection avoids tangling with the provider's
        # async ``acquire()`` context manager from inside ``to_thread``.
        # Mirrors :class:`ChunkFetcherStep._fetch_sync`.
        cache_path = getattr(self.provider, "cache_path", None)
        if cache_path is None:
            raise TypeError(
                "MemberFetcherStep requires a provider exposing 'cache_path'; "
                f"got {type(self.provider).__name__}"
            )
        where_clause = ""
        params: list = []
        if filter_sql:
            where_clause = f"WHERE {filter_sql} "
            params.extend(filter_params)
        params.append(self.limit)
        sql = _FETCH_SQL_TEMPLATE.format(where_clause=where_clause)
        conn = sqlite3.connect(str(cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "MemberFetcherStep":
        schema_name = data.get("schema_name", "member")
        if context.app_config is None:
            raise ValueError(
                "MemberFetcherStep requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        return cls(
            provider=context.connection_provider,
            allowed_fields=allowed,
            limit=data.get("limit", _DEFAULT_LIMIT),
            retriever_name=data.get("retriever_name", _DEFAULT_RETRIEVER_NAME),
        )

    def to_dict(self) -> dict:
        d: dict = {"type": "member_fetcher"}
        if self.limit != _DEFAULT_LIMIT:
            d["limit"] = self.limit
        if self.retriever_name != _DEFAULT_RETRIEVER_NAME:
            d["retriever_name"] = self.retriever_name
        return d


def _keep_by_terms(member: ModuleMember, needle: str) -> ModuleMember | None:
    """LIKE-style in-process post-filter — drop members whose name AND
    docstring miss the needle. Mirrors the legacy
    ``LikeMemberRetriever`` substring check exactly."""
    name_value = str(member.metadata.get(ModuleMemberFilterField.NAME.value, "")).lower()
    doc_value = str(member.metadata.get("docstring", "")).lower()
    if needle in name_value or needle in doc_value:
        return member
    return None


def _row_to_candidate(row: sqlite3.Row, retriever_name: str) -> ModuleMember:
    """sqlite3.Row → ModuleMember with metadata populated.

    Mirrors :func:`pydocs_mcp.storage.sqlite._row_to_module_member` for the
    metadata shape (Parameter tuple + all ModuleMemberFilterField keys),
    but stamps ``retriever_name`` so downstream stages can trace
    provenance. ``relevance`` stays ``None`` — LIKE has no rank.
    """
    raw_params = json.loads(row["parameters"] or "[]")
    params = tuple(
        Parameter(
            name=p["name"],
            annotation=p.get("annotation", ""),
            default=p.get("default", ""),
        )
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: row["package"] or "",
        ModuleMemberFilterField.MODULE.value:  row["module"] or "",
        ModuleMemberFilterField.NAME.value:    row["name"] or "",
        ModuleMemberFilterField.KIND.value:    row["kind"] or "",
        "signature":         row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters":        params,
        "docstring":         row["docstring"] or "",
    }
    return ModuleMember(
        id=row["id"],
        relevance=None,
        retriever_name=retriever_name,
        metadata=metadata,
    )


__all__ = ("MemberFetcherStep",)
