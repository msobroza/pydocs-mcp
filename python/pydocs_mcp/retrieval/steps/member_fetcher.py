"""MemberFetcherStep — candidate generation via SQLite LIKE on ``module_members``.

Single responsibility: take a query, return up to N candidate module
members whose ``name`` or ``docstring`` contains the query terms
(case-insensitive substring match). No score normalization, no top-K
cutoff, no rendering — LIKE doesn't produce relevance ranks, so
candidates carry ``relevance=None`` and downstream
:class:`TopKFilterStep` handles the cap with source-order fallback.

Pre-filter pushdown: when ``state.query.pre_filter`` is set,
:class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterStep` MUST run
upstream and write a typed
:class:`~pydocs_mcp.retrieval.steps.pre_filter.PreFilterResult`
(``tree`` + ``scope``) to
``state.scratch["pre_filter.result"]``. The fetcher reads the parsed
tree, materializes it via
:class:`pydocs_mcp.storage.protocols.FilterAdapter` (wired through
:attr:`BuildContext.filter_adapter`), and pushes the resulting WHERE
clause into the ``module_members`` SELECT. If the scratch key is
missing while the query carries a filter, the fetcher raises a clear
``RuntimeError`` pointing at the canonical YAML shape.

Mirrors the LIKE query shape the legacy ``LikeMemberRetriever`` used
(deleted in Task 9) but pushes the substring match down to SQL instead
of post-filtering in Python.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

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
from pydocs_mcp.retrieval.steps._sql_fetch import (
    execute_fetch,
    read_pre_filter_result,
    require_fetch_context,
)

if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import FilterAdapter

# Deferred storage / filter_helpers imports — see
# :mod:`pydocs_mcp.retrieval.steps.chunk_fetcher` for the rationale.
# Importing inside ``run`` breaks the storage→extraction→
# retrieval.config→retrieval.steps cycle at module-load time.


# Task 4 split fetcher from filter, Task 8 folds pre-filter pushdown into
# the fetcher (mirroring ChunkFetcherStep). The needle match is pushed
# down to SQL (LIKE on name/docstring) rather than post-filtered in
# Python: LIMIT must apply AFTER the substring match, not before, or
# matches sitting past the LIMIT window in fetch order are silently
# dropped (recall collapse on any table with more rows than `limit`).
# ``_keep_by_terms`` stays as a defense-in-depth re-check post-fetch
# (see its docstring) since ESCAPE semantics differ subtly from Python
# ``in``.
_FETCH_SQL_TEMPLATE = (
    "SELECT id, package, module, name, kind, signature, return_annotation, "
    "parameters, docstring "
    "FROM module_members "
    "{where_clause}"
    "LIMIT ?"
)

# SQLite LIKE wildcards that must be escaped so the needle is matched as a
# literal substring (parity with Python's ``needle in value``), not a
# pattern. Order matters: escape the escape character itself first.
_LIKE_ESCAPE_CHAR = "\\"
_LIKE_WILDCARDS = ("%", "_")


def _escape_like_needle(needle: str) -> str:
    """Escape ``%``/``_`` so ``LIKE ? ESCAPE '\\'`` matches literal substrings."""
    escaped = needle.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
    for wildcard in _LIKE_WILDCARDS:
        escaped = escaped.replace(wildcard, _LIKE_ESCAPE_CHAR + wildcard)
    return escaped


# WHY: single source of truth for the member-fetch defaults. Referenced
# from the dataclass field defaults + to_dict (omit-when-default) +
# from_dict (fallback when YAML omits the key).
_DEFAULT_LIMIT = 50
_DEFAULT_RETRIEVER_NAME = "like_member"

# Parameterizes the shared _sql_fetch error messages (byte-identical to the
# pre-extraction inline copies).
_STEP_LABEL = "MemberFetcherStep"


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
    filter_adapter: FilterAdapter = field(kw_only=True)
    name: str = field(default="member_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        needle = state.query.terms.strip().lower()
        if not needle:
            return replace(state, candidates=ModuleMemberList(items=()))

        result = read_pre_filter_result(
            state,
            step_label=_STEP_LABEL,
            step_name="member_fetcher",
            pipeline_yaml="pipelines/member_search.yaml",
        )
        filter_sql = ""
        filter_params: tuple = ()
        scope: frozenset[SearchScope] | None = None
        if result is not None:
            scope = result.scope
            if result.tree is not None:
                filter_sql, filter_params = self._build_where_clause(result.tree)

        conditions: list[str] = []
        params: list = []
        if filter_sql:
            conditions.append(filter_sql)
            params.extend(filter_params)
        # Push the needle match into SQL so LIMIT applies AFTER filtering
        # (see _FETCH_SQL_TEMPLATE comment) — a Python post-filter after a
        # SQL LIMIT silently truncates matches beyond the LIMIT window.
        like_needle = f"%{_escape_like_needle(needle)}%"
        conditions.append("(name LIKE ? ESCAPE '\\' OR docstring LIKE ? ESCAPE '\\')")
        params.extend((like_needle, like_needle))
        where_clause = f"WHERE {' AND '.join(conditions)} "
        params.append(self.limit)
        sql = _FETCH_SQL_TEMPLATE.format(where_clause=where_clause)
        rows = await asyncio.to_thread(
            execute_fetch, self.provider, sql, params, step_label=_STEP_LABEL
        )
        members = tuple(_row_to_candidate(row, self.retriever_name) for row in rows)
        # Defense-in-depth re-check: SQLite's LIKE is case-insensitive only
        # for ASCII by default, matching the ``.lower()`` needle here, but
        # re-applying the Python substring check keeps behavior pinned to
        # ``_keep_by_terms`` semantics regardless of SQLite build options.
        members = tuple(kept for m in members if (kept := _keep_by_terms(m, needle)) is not None)
        if scope is not None:
            # Lazy import — break the storage→extraction→retrieval.config→
            # retrieval.steps cycle (see module docstring).
            from pydocs_mcp.retrieval.filter_helpers import _matches_scope

            members = tuple(
                m
                for m in members
                if _matches_scope(
                    str(m.metadata.get(ModuleMemberFilterField.PACKAGE.value, "")),
                    scope,
                )
            )
        return replace(state, candidates=ModuleMemberList(items=members))

    def _build_where_clause(self, tree) -> tuple[str, tuple]:
        """Materialize a parsed filter tree via the wired FilterAdapter.

        ``target_field='member'`` selects the ``module_members`` whitelist
        (and the bare-column prefix, since member queries are not joined).
        WHY: retrieval steps must never import the SQLite adapter at
        runtime — the composition root wires the concrete adapter into
        ``BuildContext.filter_adapter`` (see retrieval/factories.py).
        """
        return self.filter_adapter.adapt(tree, target_field="member")

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> MemberFetcherStep:
        schema_name = data.get("schema_name", "member")
        app_config, provider = require_fetch_context(context, _STEP_LABEL)
        if context.filter_adapter is None:
            raise ValueError(
                "MemberFetcherStep requires BuildContext.filter_adapter; "
                "the composition root wires SqliteFilterAdapter() "
                "(see retrieval/factories.py)."
            )
        allowed = frozenset(app_config.metadata_schemas[schema_name])
        return cls(
            provider=provider,
            allowed_fields=allowed,
            limit=data.get("limit", _DEFAULT_LIMIT),
            retriever_name=data.get("retriever_name", _DEFAULT_RETRIEVER_NAME),
            filter_adapter=context.filter_adapter,
        )

    def to_dict(self) -> dict:
        d: dict = {"type": "member_fetcher"}
        if self.limit != _DEFAULT_LIMIT:
            d["limit"] = self.limit
        if self.retriever_name != _DEFAULT_RETRIEVER_NAME:
            d["retriever_name"] = self.retriever_name
        return d


def _keep_by_terms(member: ModuleMember, needle: str) -> ModuleMember | None:
    """Defense-in-depth substring re-check — drop members whose name AND
    docstring miss the needle. The SQL LIKE clause in ``run`` is the
    primary filter (so LIMIT applies after matching, not before); this
    keeps results pinned to Python's exact ``in`` semantics regardless of
    SQLite build-specific LIKE/ESCAPE quirks."""
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
        ModuleMemberFilterField.MODULE.value: row["module"] or "",
        ModuleMemberFilterField.NAME.value: row["name"] or "",
        ModuleMemberFilterField.KIND.value: row["kind"] or "",
        "signature": row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters": params,
        "docstring": row["docstring"] or "",
    }
    return ModuleMember(
        id=row["id"],
        relevance=None,
        retriever_name=retriever_name,
        metadata=metadata,
    )


__all__ = ("MemberFetcherStep",)
