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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydocs_mcp.application.api_search import ApiSearch
from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.file_tools import (
    FileToolsService,
    read_only_bundle_file_tools,
)
from pydocs_mcp.application.formatting import (
    format_chunks_markdown_within_budget,
    format_members_markdown_within_budget,
)
from pydocs_mcp.application.lookup_service import LookupBody, LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput, clamp_search_limit
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.pointer_grammar import strip_pointers
from pydocs_mcp.application.protocols import DecisionNavigator
from pydocs_mcp.application.search_limit import cap_search_rows, record_matches_not_shown
from pydocs_mcp.application.search_query import (
    build_search_query,
    normalize_pkg_filter_value,
    query_for_bundle,
    scope_from_string,
)
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.application.target_resolution import ResolutionEntry, TargetRewrite
from pydocs_mcp.application.workspace_target_fallback import resolve_workspace_target_fallback
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ChunkOrigin,
    ModuleMember,
    ModuleMemberFilterField,
    SearchQuery,
    SearchResponse,
)
from pydocs_mcp.multirepo import LoadedProject, select_project
from pydocs_mcp.pointer_table import PointerTableConfig
from pydocs_mcp.retrieval.config import TargetResolutionConfig
from pydocs_mcp.retrieval.config.models import _DEFAULT_SEARCH_BUDGET_TOKENS

if TYPE_CHECKING:
    from pydocs_mcp.application.reference_service import ContextNode
    from pydocs_mcp.extraction.model import DocumentNode


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
    # Filesystem grep/glob/read_file over THIS project's source tree
    # (contract §3.7-3.9). Defaults to the root-less read-only-bundle
    # service (Null-object rule) so bundle-only loads stay constructible;
    # composition roots with a stamped project_root wire the real one.
    files: FileToolsService = field(default_factory=read_only_bundle_file_tools)
    # Whether THIS bundle holds any dependency's mined decisions (#346), read
    # once when it was loaded (``decision_corpus.bundle_holds_dependency_decisions``).
    # Over such a bundle ordinary searches leave them out unless ``package=``
    # names the dependency, whatever config serves it; over any other bundle
    # every query runs exactly as before.
    holds_dependency_decisions: bool = False


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
    return cap_search_rows(survivors, limit)


async def render_single_search(
    payload: SearchInput,
    svc: ProjectServices,
    *,
    budget_tokens: int,
    pointers: PointerTableConfig,
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """Single-database search — dispatch by ``kind``.

    Renders the SAME per-hit blocks the union path renders (one located
    heading, the hit body, its pointer bundle) from the SAME ranked rows that
    become the contract-§3.2 ``items[]``. Before this, a one-project bundle
    handed the model the pipeline's composite instead — which is the top-1
    chunk body alone on any preset without a ``token_budget_formatter`` step,
    so 67 of 67 searches in the measured run named no file at all.
    """
    if payload.kind == "decision":
        # Delegate to the DecisionNavigator so decision rendering has ONE
        # authority (get_why and search_codebase(kind="decision") share it) —
        # no second decision-record render path in the search layer.
        # Before the query is built: the decision path has no result cap of
        # its own, so building one here would report a clamp it never applies.
        return await _search_decisions_in_scope(svc.decisions, payload)
    query = query_for_bundle(
        build_search_query(payload),
        payload,
        holds_dependency_decisions=svc.holds_dependency_decisions,
    )
    limit = clamp_search_limit(payload.limit)
    if payload.kind == "docs":
        response = await svc.docs.search(query)
        rows = _ranked_chunks(response, limit)
        body = format_chunks_markdown_within_budget(rows, budget_tokens, pointers=pointers)
        return body or _EMPTY_DOCS_MSG, tuple(_chunk_item(c) for c in rows), {}
    if payload.kind == "api":
        response = await svc.api.search(query)
        members = _ranked_members(response, limit)
        body = format_members_markdown_within_budget(members, budget_tokens, pointers=pointers)
        owned = [(svc, m) for m in members]
        return body or _EMPTY_API_MSG, await _member_search_items(owned), {}
    chunk_resp, member_resp = await asyncio.gather(svc.docs.search(query), svc.api.search(query))
    rows = _ranked_chunks(chunk_resp, limit)
    members = _ranked_members(member_resp, limit)
    parts = [
        format_chunks_markdown_within_budget(rows, budget_tokens, pointers=pointers),
        format_members_markdown_within_budget(members, budget_tokens, pointers=pointers),
    ]
    body = "\n\n".join(p for p in parts if p) or _EMPTY_DOCS_MSG
    member_items = await _member_search_items([(svc, m) for m in members])
    return body, tuple(_chunk_item(c) for c in rows) + member_items, {}


async def _search_decisions_in_scope(
    decisions: DecisionNavigator, payload: SearchInput
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """``search_codebase(kind="decision")`` on one project's decision layer.

    The request's ``scope`` and ``package`` are the only frozen selectors that
    reach the decision layer, and they reach it here, normalized as
    :func:`build_search_query` normalizes them for every other kind (#346).
    """
    return await decisions.search_with_items(
        payload.query,
        scope=scope_from_string(payload.scope),
        package=normalize_pkg_filter_value(payload.package),
    )


def _ranked_chunks(response: SearchResponse, limit: int) -> tuple[Chunk, ...]:
    """The per-item chunk rows behind a rendered response, capped at ``limit``.

    Prefers ``response.candidates`` (the ranked rows the token-budget formatter
    collapsed into the composite body); falls back to ``result`` for producers
    that never populate candidates. Composite formatter output is excluded —
    it is the rendered BODY, not a retrievable row.

    Both cuts that can shrink this listing are marked: the pipeline's own
    (which no surviving row reveals) and this local cap (#271).
    """
    source = response.candidates if response.candidates is not None else response.result
    rows = [
        item
        for item in source.items
        if isinstance(item, Chunk)
        and item.metadata.get(ChunkFilterField.ORIGIN.value) != ChunkOrigin.COMPOSITE_OUTPUT.value
    ]
    record_matches_not_shown(response.dropped_by_limit, limit)
    return cap_search_rows(rows, limit)


def _ranked_members(response: SearchResponse, limit: int) -> tuple[ModuleMember, ...]:
    """The per-item member rows behind a rendered response, capped at ``limit``.

    Same candidates-first rule and same cut marking as :func:`_ranked_chunks`;
    the isinstance filter also drops the composite CHUNK the member formatter
    leaves in ``result``.
    """
    source = response.candidates if response.candidates is not None else response.result
    rows = [item for item in source.items if isinstance(item, ModuleMember)]
    record_matches_not_shown(response.dropped_by_limit, limit)
    return cap_search_rows(rows, limit)


def _chunk_item(chunk: Chunk) -> dict[str, Any]:
    """One ``search_codebase`` §3.2 ``kind="chunk"`` row (schema-v15 span keys)."""
    md = chunk.metadata
    start = md.get(ChunkFilterField.START_LINE.value)
    end = md.get(ChunkFilterField.END_LINE.value)
    return {
        "kind": "chunk",
        "id": str(chunk.id) if chunk.id is not None else "",
        "qualified_name": str(md.get("qualified_name") or ""),
        "package": str(md.get(ChunkFilterField.PACKAGE.value) or ""),
        "path": str(md.get(ChunkFilterField.SOURCE_PATH.value) or "") or None,
        "start_line": start if isinstance(start, int) else None,
        "end_line": end if isinstance(end, int) else None,
        "score": float(chunk.relevance or 0.0),
    }


def _member_item(member: ModuleMember, node: DocumentNode | None) -> dict[str, Any]:
    """One ``search_codebase`` §3.2 ``kind="member"`` row; span from ``node``."""
    md = member.metadata
    module = str(md.get(ModuleMemberFilterField.MODULE.value) or "")
    name = str(md.get(ModuleMemberFilterField.NAME.value) or "")
    qname = f"{module}.{name}" if module and name else (name or module)
    return {
        "kind": "member",
        "id": str(member.id) if member.id is not None else "",
        "qualified_name": qname,
        "package": str(md.get(ModuleMemberFilterField.PACKAGE.value) or ""),
        "path": (node.source_path or None) if node is not None else None,
        "start_line": node.start_line if node is not None else None,
        "end_line": node.end_line if node is not None else None,
        "score": float(member.relevance or 0.0),
    }


async def _member_search_items(
    owned: Sequence[tuple[ProjectServices, ModuleMember]],
) -> tuple[dict[str, Any], ...]:
    """§3.2 member rows with best-effort spans via each owner's document tree.

    Trees are fetched once per ``(service, package, module)`` and the member's
    node is looked up by its ``module.name`` qualified name; any miss —
    no tree navigator, no persisted tree, no matching node — degrades that row
    to null path/span (the contract's best-effort rule, plan Task 5).
    """
    cache: dict[tuple[int, str, str], DocumentNode | None] = {}
    items: list[dict[str, Any]] = []
    for svc, member in owned:
        node = await _resolve_member_node(svc, member, cache)
        items.append(_member_item(member, node))
    return tuple(items)


async def _resolve_member_node(
    svc: ProjectServices,
    member: ModuleMember,
    cache: dict[tuple[int, str, str], DocumentNode | None],
) -> DocumentNode | None:
    """Find the member's defining tree node through its project's navigator.

    ``getattr`` guard: ``svc.lookup`` is typed :class:`LookupService` (which
    always carries ``tree_svc``), but test doubles duck-type the lookup seam —
    span resolution is advisory, so a navigator-less lookup degrades to None
    instead of demanding the full LookupService surface.
    """
    navigator = getattr(svc.lookup, "tree_svc", None)
    md = member.metadata
    package = str(md.get(ModuleMemberFilterField.PACKAGE.value) or "")
    module = str(md.get(ModuleMemberFilterField.MODULE.value) or "")
    name = str(md.get(ModuleMemberFilterField.NAME.value) or "")
    if navigator is None or not (module and name):
        return None
    key = (id(svc), package, module)
    if key not in cache:
        try:
            cache[key] = await navigator.get_tree(package, module)
        except ServiceUnavailableError:
            # NullTreeService deployment (no tree index): spans are advisory
            # on search rows — degrade to null rather than failing the search.
            cache[key] = None
    tree = cache[key]
    return tree.find_node_by_qualified_name(f"{module}.{name}") if tree is not None else None


# Extras channel key: the bundle whose lookup ANSWERED, as its db path. Under
# multi-repo with no selector the answer comes from whichever project resolves
# first by recency, which need not be the first-loaded one — and
# `get_references`' `meta.resolution` is that bundle's index-time grammar
# stamp, so the router has to know which bundle it was. The db path, not the
# project NAME: two loaded bundles may share a name, and `select_project`
# resolves a bare name to the NEWEST namesake, which need not be the one that
# answered. Internal, like TARGET_EXTENSION_EXTRA: the consumers strip it
# before the wire.
ANSWERING_BUNDLE_EXTRA: str = "answering_bundle"


async def _tagged_answer(svc: ProjectServices, body: Awaitable[LookupBody]) -> LookupBody:
    """Await ``body`` and tag its extras with the bundle that answered.

    ONE tagging site for every lookup entry — the single-project paths, the
    recency walk's exact pass, and its pass-2 workspace rewrite — so they
    cannot drift on which bundle they claim. Pass 2 is tagged too: a rewritten
    target still answers from one concrete bundle (spec 2026-09-10 §2.5), and
    ``get_references`` reads THAT bundle's index-time grammar stamp.
    """
    text, items, extras = await body
    return text, items, {**extras, ANSWERING_BUNDLE_EXTRA: str(svc.project.db_path)}


async def _answer_from(svc: ProjectServices, payload: LookupInput) -> LookupBody:
    """One project's lookup, its extras tagged with the answering bundle."""
    return await _tagged_answer(svc, svc.lookup.lookup_with_items(payload))


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
    # Token budget of the search TEXT — how many ranked hits the model reads.
    # ``search.output.budget_tokens`` in YAML; the composition root threads it.
    budget_tokens: int = _DEFAULT_SEARCH_BUDGET_TOKENS
    envelope: ResponseEnvelope | None = None
    # The deployment's pointer table. BOTH paths render their own hits here, so
    # both must read the same rows or a multi-repo hit would offer different
    # follow-ups than a single-repo one.
    pointers: PointerTableConfig = field(default_factory=PointerTableConfig)

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
        body, _items, _extras = await self._search_body(payload)
        return strip_pointers(body)

    async def _search_body(
        self, payload: SearchInput
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        if payload.project:
            svc = _select_service(self.services, payload.project)
            return await self._render_one(payload, svc)
        if len(self.services) == 1:
            return await self._render_one(payload, self.services[0])
        # kind="decision" has no cross-project union path (decisions are
        # project-local rationale, not a shared corpus): resolve to the
        # most-recently-indexed project's DecisionNavigator, mirroring the
        # recency preference the ranked-union dedup applies elsewhere.
        if payload.kind == "decision":
            newest = max(self.services, key=lambda s: s.project.indexed_at)
            return await _search_decisions_in_scope(newest.decisions, payload)

        # Built once (the clamp lands on the ledger once), then run by each
        # bundle under its own dependency-decision gate (#346).
        query = build_search_query(payload)
        queries = tuple(
            query_for_bundle(
                query, payload, holds_dependency_decisions=s.holds_dependency_decisions
            )
            for s in self.services
        )
        limit = clamp_search_limit(payload.limit)
        parts: list[str] = []
        items: list[dict[str, Any]] = []
        if payload.kind in ("docs", "any"):
            text, merged_chunks = await self._union_docs(queries, limit)
            parts.append(text)
            items.extend(_chunk_item(c) for c in merged_chunks)
        if payload.kind in ("api", "any"):
            text, owned_members = await self._union_api(queries, limit)
            parts.append(text)
            items.extend(await _member_search_items(owned_members))
        parts = [p for p in parts if p]
        return ("\n\n".join(parts) if parts else _EMPTY_DOCS_MSG), tuple(items), {}

    async def _render_one(
        self, payload: SearchInput, svc: ProjectServices
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        """One project's search, rendered with THIS deployment's budget + table."""
        return await render_single_search(
            payload, svc, budget_tokens=self.budget_tokens, pointers=self.pointers
        )

    async def _union_docs(
        self, queries: tuple[SearchQuery, ...], limit: int
    ) -> tuple[str, tuple[Chunk, ...]]:
        """``queries[i]`` is what ``self.services[i]`` runs."""
        lists = await asyncio.gather(
            *[s.docs.ranked(q) for s, q in zip(self.services, queries, strict=True)]
        )
        tagged = [
            (s.project, c) for s, cl in zip(self.services, lists, strict=True) for c in cl.items
        ]
        merged = _merge_ranked(tagged, limit)
        text = (
            format_chunks_markdown_within_budget(merged, self.budget_tokens, pointers=self.pointers)
            if merged
            else ""
        )
        return text, merged

    async def _union_api(
        self, queries: tuple[SearchQuery, ...], limit: int
    ) -> tuple[str, tuple[tuple[ProjectServices, ModuleMember], ...]]:
        """``queries[i]`` is what ``self.services[i]`` runs."""
        lists = await asyncio.gather(
            *[s.api.ranked(q) for s, q in zip(self.services, queries, strict=True)]
        )
        tagged = [
            (s.project, m) for s, ml in zip(self.services, lists, strict=True) for m in ml.items
        ]
        # Object-identity owner map: ``_merge_ranked`` drops the project tag,
        # but each surviving member's span must resolve through its OWN
        # project's tree navigator (contract §3.2 best-effort spans).
        owners = {id(m): s for s, ml in zip(self.services, lists, strict=True) for m in ml.items}
        merged = _merge_ranked(tagged, limit)
        text = (
            format_members_markdown_within_budget(
                merged, self.budget_tokens, pointers=self.pointers
            )
            if merged
            else ""
        )
        return text, tuple((owners[id(m)], m) for m in merged)


@dataclass(frozen=True, slots=True)
class MultiProjectLookup:
    """Routes a lookup to one project (``project=``) or resolves across all."""

    services: tuple[ProjectServices, ...]
    envelope: ResponseEnvelope | None = None
    # Pass-2 workspace miss rendering (spec 2026-09-10 §2.5): the merged
    # candidate cap and the miss_candidates flag. The composition root
    # (server.build_routers) threads the YAML block; the default is the model's.
    target_resolution: TargetResolutionConfig = field(default_factory=TargetResolutionConfig)

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
        body, _items, _extras = await self._lookup_body(payload)
        return strip_pointers(body)

    async def _lookup_body(
        self, payload: LookupInput
    ) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        if payload.project:
            svc = _select_service(self.services, payload.project)
            return await _answer_from(svc, payload)
        if len(self.services) == 1:
            return await _answer_from(self.services[0], payload)
        # Empty target = "list packages" — union every project's listing.
        # No §3.3 rows here: the listing is package metadata, not tree nodes.
        if not payload.target:
            listings = await asyncio.gather(*[s.lookup.lookup(payload) for s in self.services])
            joined = "\n\n".join(
                f"## Project: {s.project.name}\n\n{text}"
                for s, text in zip(self.services, listings, strict=True)
            )
            return joined, (), {}
        # A specific target lives in exactly one project — resolve by recency.
        return await self._resolve_by_recency(
            lambda svc: _tagged_answer(svc, svc.lookup.lookup_exact(payload)),
            lambda svc, rw: _tagged_answer(svc, svc.lookup.lookup_rewritten(payload, rw)),
            target=payload.target,
            entry="lookup",
        )

    async def resolve_context(
        self, target: str, project: str
    ) -> tuple[str, tuple[ContextNode, ...], dict[str, Any]]:
        """Resolve ``target`` → ``(display_target, closure_nodes, focus_row)``
        via the right project's ``LookupService.context_nodes`` (``focus_row``
        is the §3.4 items[] row) — the same project-routing /
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
            lambda svc: svc.lookup.context_nodes_exact(target),
            lambda svc, rewrite: svc.lookup.context_nodes_rewritten(rewrite),
            target=target,
            entry="context",
        )

    async def _resolve_by_recency(
        self,
        run_exact: Callable[[ProjectServices], Awaitable[_T]],
        run_rewrite: Callable[[ProjectServices, TargetRewrite], Awaitable[_T]],
        *,
        target: str,
        entry: ResolutionEntry,
    ) -> _T:
        """Pass 1: try ``run_exact(svc)`` per project most-recently-indexed
        first; return the first result that resolves, skipping projects that
        raise ``NotFoundError``. Pass 2 (only when every project misses):
        the workspace target fallback (spec 2026-09-10 §2.5), else raise
        ``NotFoundError`` carrying a search recovery pointer (spec §D1 error
        contract) plus any merged closest-name candidates.

        The token is emitted RAW here and resolved per surface on the way out:
        ``ResponseEnvelope.wrap`` catches the unwinding error and runs
        ``resolve_error_message_pointers`` over its message, so ``str(exc)``
        reaches MCP (server.py re-raises; FastMCP serializes str(exc)) and the
        CLI (``__main__`` prints ``Error: {exc}``) carrying a call the client
        can issue, in that client's own form.
        """
        ordered = sorted(self.services, key=lambda s: s.project.indexed_at, reverse=True)
        for svc in ordered:
            try:
                return await run_exact(svc)
            except NotFoundError:
                continue
        return await resolve_workspace_target_fallback(
            ordered, run_rewrite, target=target, entry=entry, rules=self.target_resolution
        )
