"""LookupService — unified dispatch for the 'lookup' MCP tool (spec §6).

Routes a single ``LookupInput.target`` string (empty / package /
package.module / package.module.symbol) to the right backing service:
``PackageLookup`` for package metadata, ``TreeService`` for file
structure, ``ReferenceService`` for the call graph.

Service deps are mandatory (post-I9 refactor).  Production composition
root (:mod:`pydocs_mcp.storage.factories`) always wires the real
:class:`TreeService` / :class:`ReferenceService`; the
:class:`NullTreeService` / :class:`NullReferenceService` stand-ins in
:mod:`null_services` exist as ready substitutes and are wired today
only by tests.  When they are wired, the Null impls raise
``ServiceUnavailableError`` with a YAML-anchored pointer (e.g.
``reference_graph.capture.enabled``) so end users hitting the failure
mode can fix it without reading release notes.

Internal structure:

- :class:`LookupTarget` — frozen value object encapsulating target-string
  parsing.  ``LookupService.lookup`` becomes a thin dispatcher over the
  parsed target shape (I1).
- :data:`_REF_GETTERS` — dispatch table mapping ``show`` strings to
  ``ref_svc`` calls; replaces a 6-level nested if/elif (I8).
- :data:`_MODULE_ID_VARIANTS` — module-level constant (S4) — single
  source of truth for the dotted-prefix probe suffixes.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    format_context,
    format_impact,
    format_package_doc,
    format_packages_list,
    format_references,
)
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.config import (
    _DEFAULT_CONTEXT_MAX_DEPTH,
    _DEFAULT_CONTEXT_RENDER,
    _DEFAULT_CONTEXT_TOKEN_BUDGET,
    _DEFAULT_IMPACT_MAX_DEPTH,
    _DEFAULT_SKELETON_BODY_RATIO,
)

if TYPE_CHECKING:
    # Structural typing targets for the two collaborator fields and the
    # ``_REF_GETTERS`` dispatch table: TreeService / NullTreeService and
    # ReferenceService / NullReferenceService all conform to the
    # navigation Protocols in ``application.protocols``.
    from pydocs_mcp.application.protocols import ReferenceNavigator, TreeNavigator
    from pydocs_mcp.application.reference_service import ContextNode
    from pydocs_mcp.storage.node_reference import NodeReference


# ── Module-level constants (S4, S20 — single source of truth) ────────────

# Module-id variants we probe for each dotted-prefix candidate.  The
# bare variant matches Python modules (``pkg.foo`` for ``pkg/foo.py``);
# the suffixed variants match doc/notebook trees whose ids preserve
# the extension to avoid PK collision with sibling .py files (sub-PR
# #5 F20).  ``lookup("pkg.foo")`` is the natural user query; without
# this fallback markdown/notebook content would be unreachable unless
# the user knew to type the ``.md`` suffix.  Order matters — bare
# variant probed first so a real Python module wins over a sibling
# doc file with the same stem (matches user intent).
_MODULE_ID_VARIANTS: tuple[str, ...] = ("", ".md", ".ipynb")


# ── LookupTarget value object (I1) ───────────────────────────────────────

# Callback shape for ``LookupTarget.parse``.  Returns
# ``(module_id, n_consumed_input_parts) | None``.  ``module_id`` is the
# full module identifier (possibly carrying a synthetic ``.md`` /
# ``.ipynb`` suffix); ``n_consumed_input_parts`` is the count of the
# user's input dotted-parts that the match covered — NOT
# ``len(module_id.split("."))``, because the synthetic suffix isn't in
# the user's input and slicing by segment count would discard a
# trailing symbol the user actually typed.
LongestModuleFn = Callable[
    [str, tuple[str, ...]],
    Awaitable["tuple[str, int] | None"],
]


@dataclass(frozen=True, slots=True)
class LookupTarget:
    """Parsed shape of a ``lookup`` target string.

    Branches the dispatcher reads:

    - ``package is None and module is None`` → empty target →
      list all indexed packages.
    - ``package is not None and module is None and len(symbol_path) == 0``
      → single-segment target → package overview.  Distinguish from
      "no module match" via the original input length (caller has it).
    - ``module is not None and len(symbol_path) == 0`` → module-only
      target → render the module tree.
    - ``module is not None and len(symbol_path) > 0`` → symbol within
      module → dispatch on ``show`` mode.

    ``consumed`` is the count of input dotted-parts the module match
    covered, kept separate from ``len(module.split("."))`` for the
    suffix-probe rationale (see :data:`_MODULE_ID_VARIANTS`).
    """

    package: str | None
    module: str | None
    consumed: int
    symbol_path: tuple[str, ...]

    @classmethod
    async def parse(
        cls,
        target: str,
        *,
        longest_module: LongestModuleFn,
    ) -> LookupTarget:
        """Parse a dotted target string into a frozen :class:`LookupTarget`.

        The ``longest_module`` callback resolves the longest indexed
        module prefix; it returns ``(module_id, n_consumed) | None`` and
        is invoked ONLY when the target has more than one dotted segment
        — single-segment targets are package-overview requests that
        don't need a module probe (avoids a backend round-trip for the
        common "what's in package X" case).
        """
        if not target:
            return cls(package=None, module=None, consumed=0, symbol_path=())
        parts = tuple(target.split("."))
        package = parts[0]
        if len(parts) == 1:
            return cls(
                package=package,
                module=None,
                consumed=1,
                symbol_path=(),
            )
        match = await longest_module(package, parts)
        if match is None:
            # Multi-segment target with no module match — the dispatcher
            # raises ``NotFoundError`` using the original target string.
            # We collapse to "package-only" shape and leave symbol_path
            # empty so callers don't accidentally treat unresolved parts
            # as a symbol path.
            return cls(
                package=package,
                module=None,
                consumed=1,
                symbol_path=(),
            )
        module, consumed = match
        return cls(
            package=package,
            module=module,
            consumed=consumed,
            symbol_path=parts[consumed:],
        )


# ── Reference-graph dispatch table (I8) ──────────────────────────────────

# Lambdas wrap each ``ref_svc`` method so the dispatch table has a
# uniform ``(svc, package, node_id) → awaitable`` shape.  ``inherits``
# routes through ``find_by_name`` (the INHERITS reference graph is the
# source of truth post-#5c) with a kind filter; ``package`` is
# informational for ``callers`` / ``callees`` (storage is
# cross-package per spec §6.2) but is part of the 2-arg call signature
# pinned by Decision C1.
_REF_GETTERS: dict[
    str,
    Callable[
        [ReferenceNavigator, str, str],
        Awaitable[tuple[NodeReference, ...]],
    ],
] = {
    "callers": lambda svc, p, n: svc.callers(p, n),
    "callees": lambda svc, p, n: svc.callees(p, n),
    "inherits": lambda svc, _p, n: svc.find_by_name(
        n,
        kind=ReferenceKind.INHERITS,
    ),
}

# Show modes that render the page-index JSON for a tree/node.
_TREE_SHOWS: frozenset[str] = frozenset({"default", "tree"})


# ── LookupService ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LookupService:
    """Routes lookup targets to the right backing service.

    Post-I9: ``tree_svc`` and ``ref_svc`` are mandatory parameters —
    no more ``if X is None:`` guards in the dispatcher.  The Null
    impls in :mod:`pydocs_mcp.application.null_services` exist as
    ready stand-ins that preserve the user-visible
    ``ServiceUnavailableError`` contract; today only test code wires
    them.  Production composition root
    (:mod:`pydocs_mcp.storage.factories`) always wires the real
    :class:`TreeService` / :class:`ReferenceService`.  A future
    deployment that opts out of tree indexing or reference capture
    (via YAML config) could swap the Null impls in at the composition
    root without touching this class.
    """

    package_lookup: PackageLookup
    tree_svc: TreeNavigator
    ref_svc: ReferenceNavigator
    # Reverse-traversal depth for ``show="impact"``. NOT an MCP param — the
    # composition root threads ``reference_graph.impact.max_depth`` here; the
    # default keeps direct/test construction working (single source of truth:
    # ``retrieval.config._DEFAULT_IMPACT_MAX_DEPTH``).
    impact_max_depth: int = _DEFAULT_IMPACT_MAX_DEPTH
    # Forward-closure depth + token budget for ``show="context"`` — same
    # posture (YAML tunables via the composition root, not MCP params).
    context_max_depth: int = _DEFAULT_CONTEXT_MAX_DEPTH
    context_token_budget: int = _DEFAULT_CONTEXT_TOKEN_BUDGET
    # Render strategy + skeleton body budget for ``show="context"`` (spec §D6).
    # Skeleton is the shipped default; ``format_context`` reads both.
    context_render: str = _DEFAULT_CONTEXT_RENDER
    context_body_ratio: float = _DEFAULT_SKELETON_BODY_RATIO

    async def lookup(self, payload: LookupInput) -> str:
        target_str = payload.target
        parsed = await LookupTarget.parse(
            target_str,
            longest_module=self._longest_module,
        )

        # 1. Empty target → list all indexed packages.
        if parsed.package is None:
            packages = await self.package_lookup.list_packages()
            return format_packages_list(packages)

        # 2. Single-segment target → package overview.  Distinguish via
        # the original input: if the user typed a multi-segment target
        # and we collapsed to "package-only" shape, the module probe
        # didn't match and we raise NotFoundError.
        original_parts = target_str.split(".")
        if parsed.module is None:
            if len(original_parts) == 1:
                doc = await self.package_lookup.get_package_doc(parsed.package)
                if doc is None:
                    raise NotFoundError(f"package '{parsed.package}' not indexed")
                return format_package_doc(doc)
            # Multi-segment target but no module match → NotFoundError
            # using the user's original string (preserves the pre-refactor
            # message shape).
            raise NotFoundError(f"no module matching '{target_str}' found under '{parsed.package}'")

        # 3. Module-only target → render module tree.
        if not parsed.symbol_path:
            return await self._module_lookup(parsed.package, parsed.module)

        # 4. Symbol lookup — ``limit`` flows down into the reference-graph
        # branches so YAML-tuned ``reference_graph.output.default_limit``
        # caps the rendered row count.
        return await self._symbol_lookup(
            parsed.package,
            parsed.module,
            target_str,
            payload.show,
            payload.limit,
        )

    async def _module_lookup(self, package: str, module: str) -> str:
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no tree stored for '{package}.{module}'")
        return json.dumps(tree.to_pageindex_json(), indent=2)

    async def _symbol_lookup(
        self,
        package: str,
        module: str,
        target: str,
        show: str,
        limit: int,
    ) -> str:
        """Resolve a symbol within a known module and render per ``show``.

        Replaces the pre-refactor 6-level nested if/elif with
        guard-clauses + a :data:`_REF_GETTERS` dispatch table (I8):
        each branch returns early, and the reference-graph dispatch
        is one table lookup + one ``await``.
        """
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for '{package}.{module}'")
        node = tree.find_node_by_qualified_name(target)
        if node is None:
            raise NotFoundError(f"'{target}' not found in {module}")

        # Tree / default → render node's page-index JSON.
        if show in _TREE_SHOWS:
            return json.dumps(node.to_pageindex_json(), indent=2)

        # Ranked blast-radius — multi-hop REVERSE traversal, its own return
        # shape (ranked ImpactNodes) + formatter, so it can't ride _REF_GETTERS.
        # Applies to any node kind (unlike ``inherits``). ``limit`` slices the
        # rendered rows AFTER the service ranks the full discovered set.
        if show == "impact":
            impacted = await self.ref_svc.impact(
                package,
                node.node_id,
                max_depth=self.impact_max_depth,
                limit=limit,
            )
            return format_impact(impacted, target=target, limit=limit)

        # Smart-context — forward dependency-closure packed at graded fidelity
        # under a token budget. Own return shape (ContextNodes) + formatter.
        # ``limit`` caps the candidate node count; the token budget caps output.
        # Composed from the two reusable helpers ``get_context`` calls directly
        # for its proportional multi-target budget split — behavior here is
        # unchanged (full ``context_token_budget`` for the single-target path).
        if show == "context":
            ctx = await self._context_closure(package, node.node_id, limit=limit)
            return self.render_context_card(target, ctx, token_budget=self.context_token_budget)

        # Reference-graph dispatch (callers / callees / inherits).
        getter = _REF_GETTERS.get(show)
        if getter is None:
            raise InvalidArgumentError(f"unknown show value: {show}")

        if show == "inherits" and node.kind != "class":
            raise InvalidArgumentError(
                f"show='inherits' only applies to CLASS nodes, got {node.kind}"
            )

        # Null impls (deployments without the reference graph) raise
        # ``ServiceUnavailableError`` with the YAML-anchored message
        # from this same call site — the dispatcher stays branch-free.
        rows = await getter(self.ref_svc, package, node.node_id)

        # Cap before render — the service may return more than ``limit``
        # rows (cross-package fan-in is unbounded).  We do the slice
        # here so format_references receives the same bound we'll
        # surface to the user.
        if len(rows) > limit:
            rows = rows[:limit]
        return format_references(
            rows,
            target=target,
            show=show,
            limit=limit,
        )

    async def context_nodes(self, target: str) -> tuple[str, tuple[ContextNode, ...]]:
        """Resolve ``target`` to its forward dependency closure.

        Runs the same parse + node-resolution path as ``lookup(show="context")``
        but stops BEFORE rendering, returning ``(display_target, nodes)`` so the
        caller can render one card per target under a per-card token budget
        (``ToolRouter.get_context``'s proportional multi-target split). ``target``
        is echoed back verbatim as the card heading target — matching the
        single-target ``show="context"`` path.

        ``NotFoundError`` propagates unchanged (bad package / module / symbol),
        as does ``ServiceUnavailableError`` from a ``NullReferenceService``.
        """
        package, node_id = await self._resolve_context_target(target)
        # ``limit`` mirrors the single-target ``lookup(show="context")`` path,
        # which flows the ``LookupInput.limit`` default (single source of truth:
        # ``reference_graph.output.default_limit`` via the YAML-backed slot).
        limit = LookupInput(target=target).limit
        nodes = await self._context_closure(package, node_id, limit=limit)
        return target, nodes

    def render_context_card(
        self,
        target: str,
        nodes: tuple[ContextNode, ...],
        *,
        token_budget: int,
    ) -> str:
        """Render one context card via ``format_context`` at ``token_budget``.

        Threads the service's render strategy + skeleton body ratio so a card
        rendered here is byte-identical to the single-target path at the same
        budget. Pure (no I/O) — the ``get_context`` split renders every card
        with the same helper at each target's proportional share.
        """
        return format_context(
            nodes,
            target=target,
            token_budget=token_budget,
            render=self.context_render,
            body_ratio=self.context_body_ratio,
        )

    async def _context_closure(
        self, package: str, node_id: str, *, limit: int
    ) -> tuple[ContextNode, ...]:
        """The ``ref_svc.context`` call, shared by the sync and split paths."""
        return await self.ref_svc.context(
            package,
            node_id,
            max_depth=self.context_max_depth,
            limit=limit,
        )

    async def _resolve_context_target(self, target: str) -> tuple[str, str]:
        """Parse ``target`` → ``(package, node_id)`` for a symbol-level context
        request. Raises ``NotFoundError`` with the same messages the ``lookup``
        dispatcher surfaces (unresolved package / module / symbol)."""
        parsed = await LookupTarget.parse(target, longest_module=self._longest_module)
        if parsed.module is None or not parsed.symbol_path:
            raise NotFoundError(f"no symbol matching '{target}' found for context closure")
        tree = await self.tree_svc.get_tree(parsed.package, parsed.module)
        if tree is None:
            raise NotFoundError(f"no tree for '{parsed.package}.{parsed.module}'")
        node = tree.find_node_by_qualified_name(target)
        if node is None:
            raise NotFoundError(f"'{target}' not found in {parsed.module}")
        return parsed.package, node.node_id

    async def _longest_module(
        self,
        package: str,
        parts: tuple[str, ...],
    ) -> tuple[str, int] | None:
        """Adapter that exposes :meth:`_longest_indexed_module` to
        :meth:`LookupTarget.parse` with the callback shape it expects.

        Kept as a thin wrapper rather than passing the method directly
        so the parse logic stays decoupled from how we walk dotted
        prefixes — a future refactor can swap the walker without
        touching ``LookupTarget``.
        """
        return await self._longest_indexed_module(package, list(parts))

    async def _longest_indexed_module(
        self,
        package: str,
        parts: list[str],
    ) -> tuple[str, int] | None:
        """Walk longest-prefix-first; return ``(module_id, n)`` where n is
        the count of input parts consumed by the match, or ``None``.

        For each dotted-prefix candidate we probe three module-id shapes:
        the bare name (``pkg.foo`` — Python module), and the two
        doc/notebook suffixed forms (``pkg.foo.md`` / ``pkg.foo.ipynb``).
        The bare form is tried first so a real Python module wins over
        a sibling doc file with the same stem, matching the user's most
        likely intent.  The suffix is ours, never the user's — so
        ``consumed`` reflects the prefix length ``i`` regardless of
        which variant matched (the caller uses ``consumed`` to slice
        ``parts`` for the symbol-path remainder).

        Probes ``tree_svc.exists`` first (cheap row check, no JSON
        parse); if the Null impl is wired ``exists`` returns ``False``
        and we fall through to ``PackageLookup.find_module`` — the
        no-tree-index deployment still resolves modules.

        Example (spec S30) — target ``"fastapi.routing.APIRouter.include_router"``:

        * Caller (:meth:`LookupTarget.parse`) splits to
          ``parts = ["fastapi", "routing", "APIRouter", "include_router"]``
          and dispatches ``package = "fastapi"``.
        * If only ``fastapi.routing`` is indexed (the class + method are NOT
          separate modules in the tree), this walk tries ``i=4`` → ``i=3``
          → ``i=2`` and matches at ``i=2`` with ``module_id = "fastapi.routing"``.
        * Returns ``("fastapi.routing", 2)`` — ``consumed == 2`` indexes
          INTO ``parts`` so the caller's ``symbol_path`` becomes
          ``parts[2:] = ("APIRouter", "include_router")``. Note that
          ``consumed`` is the prefix LENGTH (including the package name
          slot at ``parts[0]``), NOT ``len(module.split('.'))`` of just
          the post-package suffix.
        """
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            for suffix in _MODULE_ID_VARIANTS:
                variant = candidate + suffix
                if await self.tree_svc.exists(package, variant):
                    return (variant, i)
                if await self.package_lookup.find_module(package, variant):
                    return (variant, i)
        return None
