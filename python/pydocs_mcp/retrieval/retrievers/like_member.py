"""LikeMemberRetriever — LIKE-based retriever over ``module_members``.

``pre_filter`` (e.g. ``{"package": "fastapi"}``) is parsed via
``format_registry`` and pushed down to ``store.list(filter=...)`` — the
safe-column allowlist on :class:`SqliteModuleMemberRepository` rejects
unknown columns before SQL is emitted. ``query.terms`` then filters the
returned rows against ``name`` / ``docstring`` substrings in-process,
since ``ModuleMemberStore`` offers no text-search contract yet
(spec §5.7).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.models import (
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.retrievers._shared import (
    _matches_scope,
    _schema_from_fields,
    _split_scope,
)
from pydocs_mcp.retrieval.serialization import BuildContext, retriever_registry
from pydocs_mcp.storage.filters import Filter, format_registry

if TYPE_CHECKING:
    from pydocs_mcp.storage.sqlite import SqliteModuleMemberRepository


@retriever_registry.register("like_member")
@dataclass(frozen=True, slots=True)
class LikeMemberRetriever:
    store: "SqliteModuleMemberRepository"
    allowed_fields: frozenset[str]
    name: str = "like_member"
    schema_name: str = "member"

    async def retrieve(self, query: SearchQuery) -> ModuleMemberList:
        tree: Filter | None = None
        scope: frozenset[SearchScope] | None = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            _schema_from_fields(self.allowed_fields).validate(tree)
            tree, scope = _split_scope(tree)
        rows = await self.store.list(filter=tree, limit=query.max_results)
        needle = query.terms.lower()
        items: list[ModuleMember] = []
        for member in rows:
            member_pkg = str(member.metadata.get(ModuleMemberFilterField.PACKAGE.value, ""))
            if scope is not None and not _matches_scope(member_pkg, scope):
                continue
            name_value = str(member.metadata.get(ModuleMemberFilterField.NAME.value, "")).lower()
            doc_value = str(member.metadata.get("docstring", "")).lower()
            if needle in name_value or needle in doc_value:
                items.append(
                    ModuleMember(
                        id=member.id,
                        relevance=None,
                        retriever_name=self.name,
                        metadata=dict(member.metadata),
                    )
                )
        return ModuleMemberList(items=tuple(items))

    def to_dict(self) -> dict:
        return {"type": "like_member", "schema_name": self.schema_name}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LikeMemberRetriever":
        schema_name = data.get("schema_name", "member")
        app_config = context.app_config
        if app_config is None:
            raise ValueError(
                "LikeMemberRetriever requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        module_member_repo = context.module_member_store
        if module_member_repo is None:
            raise ValueError(
                "LikeMemberRetriever requires BuildContext.module_member_store; "
                "provide SqliteModuleMemberRepository at server/CLI startup."
            )
        allowed = frozenset(app_config.metadata_schemas[schema_name])
        return cls(store=module_member_repo, allowed_fields=allowed, schema_name=schema_name)


__all__ = ("LikeMemberRetriever",)
