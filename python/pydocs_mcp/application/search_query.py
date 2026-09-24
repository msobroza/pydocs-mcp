"""Build a backend-neutral ``SearchQuery`` from an MCP ``SearchInput``.

Extracted from ``server.py`` so the single-db search path (``server._do_search``)
and the multi-repo router (``application.multi_project_search``) share ONE
input→query translation — scope/package become the pre-filter the retrieval
pipeline pushes down. Keeping it here avoids a server↔router import cycle.
"""

from __future__ import annotations

from dataclasses import replace

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.search_limit import effective_search_limit
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


def build_search_query(payload: SearchInput, *, branch: str = "") -> SearchQuery:
    """One ``SearchQuery`` shape works for chunks, members, or both — the
    filter-key strings overlap across ``ChunkFilterField`` and
    ``ModuleMemberFilterField``.

    ``max_results`` carries the client's ``limit`` (bounded by
    ``search.output.max_limit``) so the pipeline's limit step caps at what the
    caller asked for instead of its own YAML default — before this the limit
    never left the application layer and every search returned eight rows
    (#271). Call this ONCE per response: the clamp it applies is recorded on
    the response's truncation ledger. Each loaded bundle then runs it through
    :func:`query_for_bundle`.

    ``branch`` is the branch this search pins (spec §6.4, #312) — see
    :func:`pinned_to_branch`; ``""`` keeps today's query byte for byte.
    """
    pre_filter: dict = {ChunkFilterField.SCOPE.value: scope_from_string(payload.scope).value}
    if payload.package:
        pre_filter[ChunkFilterField.PACKAGE.value] = normalize_pkg_filter_value(payload.package)
    # kind="decision" narrows the corpus to mined decision-record chunks; the
    # origin pushdown both scopes retrieval and lets the YAML router select the
    # decision_search preset (kind_is_decision reads this same key).
    if payload.kind == "decision":
        pre_filter[ChunkFilterField.ORIGIN.value] = ChunkOrigin.DECISION_RECORD.value
    query = SearchQuery(
        terms=payload.query,
        max_results=effective_search_limit(payload.limit),
        pre_filter=pre_filter,
    )
    return pinned_to_branch(query, branch)


def pinned_to_branch(query: SearchQuery, branch: str) -> SearchQuery:
    """``query`` pinned to ``branch`` (spec §6.4, #312); ``""`` returns it as is.

    The pin rides beside ``pre_filter`` (``SearchQuery.branch``), and the
    pre-filter step ANDs it into every fetcher's tree after validating the
    request's own filter. Per bundle: on a multi-repo union each loaded bundle
    pins the branch its own directory resolved.
    """
    return replace(query, branch=branch) if branch else query


def query_for_bundle(
    query: SearchQuery, payload: SearchInput, *, holds_dependency_decisions: bool
) -> SearchQuery:
    """``query`` as ONE loaded bundle runs it (#346).

    ``holds_dependency_decisions`` is what that bundle holds, read once when it
    was loaded (``ProjectServices.holds_dependency_decisions``), whatever config
    serves it. Over such a bundle an ordinary search asks the pipeline to leave
    dependency decisions out; over any other bundle ``query`` comes back as is,
    so a stock bundle's filter and dense ANN path never move.
    """
    if holds_dependency_decisions and _leaves_dependency_decisions_out(payload):
        return replace(query, exclude_dependency_decisions=True)
    return query


def _leaves_dependency_decisions_out(payload: SearchInput) -> bool:
    """Whether a search must leave dependency decisions out (#346).

    ``kind="decision"`` keeps its own corpus rule (``decision_corpus.py``).
    Every other search leaves them out unless its ``package=`` names a
    dependency: ``scope="deps"`` alone does not ask, so a library's reasoning
    never rides in on a broad search.
    """
    if payload.kind == "decision":
        return False
    package = payload.package.strip()
    return not package or normalize_pkg_filter_value(package) == PROJECT_PACKAGE_NAME
