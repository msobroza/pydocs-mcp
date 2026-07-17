"""Multi-repo router — route or union search/lookup across loaded projects.

Wraps N per-project service sets (one ``DocsSearch`` / ``ApiSearch`` /
``LookupService`` per loaded ``.db``) behind the same call shape the single-db
path uses. A ``project=`` scope routes to one project (behaving exactly like a
single-db server); an empty scope unions across all loaded projects and dedups.

Dedup rule (when the same symbol appears in several loaded dbs): a root-project
copy (``package == __project__``) beats a dependency copy; among dependency
duplicates the most-recently-indexed wins. The final ranking is by score —
comparable across dbs because the embedder-match guard forces one embedder.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from pydocs_mcp.application.api_search import ApiSearch
from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
    pointer_token,
    render_top_composite,
    strip_pointers,
)
from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import InvalidArgumentError, NotFoundError
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.protocols import DecisionNavigator
from pydocs_mcp.application.search_query import build_search_query
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ModuleMember
from pydocs_mcp.multirepo import LoadedProject, select_project

if TYPE_CHECKING:
    from pydocs_mcp.application.reference_service import ContextNode

# Composite token budget for the unioned output — matches the shipped
# chunk_search_graph.yaml / member_search.yaml ``budget: 2000``.
_DEFAULT_BUDGET_TOKENS = 2000

# Empty-result bodies (single source of truth). ``search`` returns success with
# one of these strings — it never raises (unlike ``lookup``; see mcp_errors.py).
# ``ToolRouter`` reads ``EMPTY_SEARCH_MESSAGES`` to append the zero-hit overview
# pointer (spec §D1 empty contract) without re-encoding the literals.
_EMPTY_DOCS_MSG = "No matches found."
_EMPTY_API_MSG = "No symbols found."
EMPTY_SEARCH_MESSAGES = frozenset((_EMPTY_DOCS_MSG, _EMPTY_API_MSG))

# A ranked candidate is a Chunk or a ModuleMember (both carry ``relevance`` +
# ``metadata``). Value-constrained so the merge preserves the concrete type and
# the chunk / member formatters (each wanting its own tuple) type-check.
_R = TypeVar("_R", Chunk, ModuleMember)

# Result of a per-project resolver run through ``_resolve_by_recency`` — a
# rendered lookup body (``str``) or a ``(target, closure)`` context tuple.
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ProjectServices:
    """One loaded project's full read-side service set, plus its identity."""

    project: LoadedProject
    docs: DocsSearch
    api: ApiSearch
    lookup: LookupService
    symbol_source: SymbolSourceService
    overview: OverviewService
    decisions: DecisionNavigator


def _dedup_identity(project_name: str, metadata: Mapping[str, Any]) -> tuple[tuple[str, str], bool]:
    """Return ``((effective_package, symbol_key), is_root)`` for a candidate.

    ``__project__`` is normalized to the loaded project's name so a symbol that is
    a project's own code in one db and a dependency in another collapses to one
    dedup group (letting root beat dependency).
    """
    pkg = str(metadata.get("package", ""))
    is_root = pkg == PROJECT_PACKAGE_NAME
    eff_pkg = project_name if is_root else pkg
    qn = str(metadata.get("qualified_name") or "")
    if is_root and qn.startswith(PROJECT_PACKAGE_NAME):
        qn = project_name + qn[len(PROJECT_PACKAGE_NAME) :]
    tail = qn or "\0".join(
        (
            str(metadata.get("module", "")),
            str(metadata.get("name", "")),
            str(metadata.get("title", "")),
        )
    )
    return (eff_pkg, tail), is_root


def _merge_ranked(tagged: list[tuple[LoadedProject, _R]], limit: int) -> tuple[_R, ...]:
    """Dedup + rank candidates gathered from several projects.

    Within a dedup group the survivor is chosen by ``(is_root, indexed_at,
    score)`` — root beats dependency, then most-recently-indexed, then higher
    score. Survivors are ranked by score (best first) and truncated to ``limit``.
    """
    best: dict[tuple[str, str], tuple[tuple[bool, float, float], _R]] = {}
    for project, obj in tagged:
        key, is_root = _dedup_identity(project.name, obj.metadata)
        priority = (is_root, project.indexed_at, obj.relevance or 0.0)
        current = best.get(key)
        if current is None or priority > current[0]:
            best[key] = (priority, obj)
    survivors = [obj for _, obj in best.values()]
    survivors.sort(key=lambda o: o.relevance or 0.0, reverse=True)
    return tuple(survivors[:limit])


async def render_single_search(
    payload: SearchInput,
    docs: DocsSearch,
    api: ApiSearch,
    decisions: DecisionNavigator,
) -> str:
    """Single-database search render — dispatch by ``kind`` (the ``server._do_search``
    behavior, shared so a 1-project router is byte-identical to a single-db server)."""
    query = build_search_query(payload)
    if payload.kind == "decision":
        # Delegate to the DecisionNavigator so decision rendering has ONE
        # authority (get_why and search_codebase(kind="decision") share it) —
        # no second decision-record render path in the search layer.
        return await decisions.search(payload.query)
    if payload.kind == "docs":
        return render_top_composite(await docs.search(query), empty_msg=_EMPTY_DOCS_MSG)
    if payload.kind == "api":
        return render_top_composite(await api.search(query), empty_msg=_EMPTY_API_MSG)
    chunk_resp, member_resp = await asyncio.gather(docs.search(query), api.search(query))
    parts = [
        render_top_composite(chunk_resp, empty_msg=""),
        render_top_composite(member_resp, empty_msg=""),
    ]
    parts = [p for p in parts if p]
    return "\n\n".join(parts) if parts else _EMPTY_DOCS_MSG


def _select_service(services: tuple[ProjectServices, ...], project_name: str) -> ProjectServices:
    """Resolve the one ``ProjectServices`` whose loaded db matches ``project_name``.

    ``select_project`` raises ``KeyError`` for an unknown name — that's a
    storage-layer detail, not an MCP error type. Left uncaught it would fall
    through server._run_tool's generic ``except Exception`` arm and surface
    as ``ServiceUnavailableError``, misleading a client into believing the
    server is down rather than that their own ``project=`` argument was
    wrong. Re-raise as ``InvalidArgumentError`` (typed, passes through
    _run_tool unchanged) so a bad selector reads as a client-input error.
    """
    try:
        chosen = select_project([s.project for s in services], project_name)
    except KeyError as e:
        raise InvalidArgumentError(str(e)) from e
    return next(s for s in services if s.project.db_path == chosen.db_path)


@dataclass(frozen=True, slots=True)
class MultiProjectSearch:
    """Routes a search to one project (``project=``) or unions across all."""

    services: tuple[ProjectServices, ...]
    budget_tokens: int = _DEFAULT_BUDGET_TOKENS
    envelope: ResponseEnvelope | None = None

    async def search(self, payload: SearchInput) -> str:
        if self.envelope is not None:
            # Legacy str surface: the ToolRouter owns the structured envelope;
            # this path keeps its pre-ToolResponse text-only contract.
            wrapped = await self.envelope.wrap(
                "search_codebase",
                payload.project or self.services[0].project.name,
                lambda: self._search_body(payload),
            )
            return wrapped.text
        # Legacy/no-envelope path: never leak raw pointer tokens.
        return strip_pointers(await self._search_body(payload))

    async def _search_body(self, payload: SearchInput) -> str:
        if payload.project:
            svc = _select_service(self.services, payload.project)
            return await render_single_search(payload, svc.docs, svc.api, svc.decisions)
        if len(self.services) == 1:
            svc = self.services[0]
            return await render_single_search(payload, svc.docs, svc.api, svc.decisions)
        # kind="decision" has no cross-project union path (decisions are
        # project-local rationale, not a shared corpus): resolve to the
        # most-recently-indexed project's DecisionNavigator, mirroring the
        # recency preference the ranked-union dedup applies elsewhere.
        if payload.kind == "decision":
            newest = max(self.services, key=lambda s: s.project.indexed_at)
            return await newest.decisions.search(payload.query)

        query = build_search_query(payload)
        parts: list[str] = []
        if payload.kind in ("docs", "any"):
            parts.append(await self._union_docs(query, payload.limit))
        if payload.kind in ("api", "any"):
            parts.append(await self._union_api(query, payload.limit))
        parts = [p for p in parts if p]
        return "\n\n".join(parts) if parts else _EMPTY_DOCS_MSG

    async def _union_docs(self, query, limit: int) -> str:
        lists = await asyncio.gather(*[s.docs.ranked(query) for s in self.services])
        tagged = [
            (s.project, c) for s, cl in zip(self.services, lists, strict=True) for c in cl.items
        ]
        merged = _merge_ranked(tagged, limit)
        return format_chunks_markdown_within_budget(merged, self.budget_tokens) if merged else ""

    async def _union_api(self, query, limit: int) -> str:
        lists = await asyncio.gather(*[s.api.ranked(query) for s in self.services])
        tagged = [
            (s.project, m) for s, ml in zip(self.services, lists, strict=True) for m in ml.items
        ]
        merged = _merge_ranked(tagged, limit)
        return format_members_markdown_within_budget(merged, self.budget_tokens) if merged else ""


@dataclass(frozen=True, slots=True)
class MultiProjectLookup:
    """Routes a lookup to one project (``project=``) or resolves across all."""

    services: tuple[ProjectServices, ...]
    envelope: ResponseEnvelope | None = None

    async def lookup(self, payload: LookupInput) -> str:
        if self.envelope is not None:
            # Legacy str surface (deprecated `lookup` alias) — text only.
            wrapped = await self.envelope.wrap(
                "lookup",
                payload.project or self.services[0].project.name,
                lambda: self._lookup_body(payload),
            )
            return wrapped.text
        # Legacy/no-envelope path: never leak raw pointer tokens.
        return strip_pointers(await self._lookup_body(payload))

    async def _lookup_body(self, payload: LookupInput) -> str:
        if payload.project:
            return await _select_service(self.services, payload.project).lookup.lookup(payload)
        if len(self.services) == 1:
            return await self.services[0].lookup.lookup(payload)
        # Empty target = "list packages" — union every project's listing.
        if not payload.target:
            listings = await asyncio.gather(*[s.lookup.lookup(payload) for s in self.services])
            return "\n\n".join(
                f"## Project: {s.project.name}\n\n{text}"
                for s, text in zip(self.services, listings, strict=True)
            )
        # A specific target lives in exactly one project — resolve by recency.
        return await self._resolve_by_recency(
            lambda svc: svc.lookup.lookup(payload),
            target=payload.target,
        )

    async def resolve_context(
        self, target: str, project: str
    ) -> tuple[str, tuple[ContextNode, ...]]:
        """Resolve ``target`` → ``(display_target, closure_nodes)`` via the right
        project's ``LookupService.context_nodes`` — the same project-routing /
        recency resolution ``_lookup_body`` uses, so a batched ``get_context``
        target lands in exactly the project a single lookup would.

        ``ToolRouter.get_context`` calls this once per target (phase 1) before
        splitting the shared budget across the gathered closures (phase 2).
        """
        if project:
            return await _select_service(self.services, project).lookup.context_nodes(target)
        if len(self.services) == 1:
            return await self.services[0].lookup.context_nodes(target)
        return await self._resolve_by_recency(
            lambda svc: svc.lookup.context_nodes(target),
            target=target,
        )

    async def _resolve_by_recency(
        self, run: Callable[[ProjectServices], Awaitable[_T]], *, target: str
    ) -> _T:
        """Try ``run(svc)`` per project most-recently-indexed first; return the
        first result that resolves, skipping projects that raise
        ``NotFoundError``. When every project misses, raise ``NotFoundError``
        carrying a search recovery pointer (spec §D1 error contract).

        The token stays RAW in the surfaced message: a raised error unwinds past
        ``ResponseEnvelope.wrap`` before its resolve_pointers step runs
        (envelope.py resolves only the value returned by produce(), never an
        exception), so the literal ``[[next:search:...]]`` is what ``str(exc)``
        yields on both surfaces — MCP (server.py re-raises; FastMCP serializes
        str(exc)) and CLI (__main__ prints ``Error: {exc}``).
        """
        ordered = sorted(self.services, key=lambda s: s.project.indexed_at, reverse=True)
        for svc in ordered:
            try:
                return await run(svc)
            except NotFoundError:
                continue
        raise NotFoundError(
            f"'{target}' not found in any loaded project. "
            f"{pointer_token('search', target.rsplit('.', 1)[-1])}"
        )
