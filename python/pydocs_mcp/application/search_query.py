"""Build a backend-neutral ``SearchQuery`` from an MCP ``SearchInput``.

Extracted from ``server.py`` so the single-db search path (``server._do_search``)
and the multi-repo router (``application.multi_project_search``) share ONE
input→query translation — scope/package become the pre-filter the retrieval
pipeline pushes down. Keeping it here avoids a server↔router import cycle.
"""

from __future__ import annotations

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkFilterField,
    ChunkOrigin,
    SearchQuery,
    SearchScope,
)


def scope_from_string(scope: str) -> SearchScope:
    """Map the ``SearchInput.scope`` literal to the ``SearchScope`` enum."""
    return {
        "project": SearchScope.PROJECT_ONLY,
        "deps": SearchScope.DEPENDENCIES_ONLY,
        "all": SearchScope.ALL,
    }[scope]


def normalize_pkg_filter_value(package: str) -> str:
    """PyPI names like 'Flask-Login' are stored as 'flask_login'; leave the
    ``__project__`` sentinel intact."""
    pkg = package.strip()
    return pkg if pkg == PROJECT_PACKAGE_NAME else normalize_package_name(pkg)


def build_search_query(payload: SearchInput) -> SearchQuery:
    """One ``SearchQuery`` shape works for chunks, members, or both — the
    filter-key strings overlap across ``ChunkFilterField`` and
    ``ModuleMemberFilterField``."""
    pre_filter: dict = {ChunkFilterField.SCOPE.value: scope_from_string(payload.scope).value}
    if payload.package:
        pre_filter[ChunkFilterField.PACKAGE.value] = normalize_pkg_filter_value(payload.package)
    # kind="decision" narrows the corpus to mined decision-record chunks; the
    # origin pushdown both scopes retrieval and lets the YAML router select the
    # decision_search preset (kind_is_decision reads this same key).
    if payload.kind == "decision":
        pre_filter[ChunkFilterField.ORIGIN.value] = ChunkOrigin.DECISION_RECORD.value
    return SearchQuery(terms=payload.query, pre_filter=pre_filter)
