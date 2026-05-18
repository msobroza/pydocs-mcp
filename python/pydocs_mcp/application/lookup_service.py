"""LookupService — unified dispatch for the 'lookup' MCP tool (spec §6).

Routes a single ``LookupInput.target`` string (empty / package /
package.module / package.module.symbol) to the right backing service:
``PackageLookup`` for package metadata, ``TreeService`` (optional) for
file structure, ``ReferenceService`` (optional) for the call graph.

Soft dependencies — when ``tree_svc`` or ``ref_svc`` is None, ``show``
modes that need them raise ``ServiceUnavailableError``. ``inherits`` is
unified with ``callers``/``callees`` behind the reference graph so all
three need ``ref_svc``; the error message points at the YAML knob
(``reference_graph.capture.enabled``) so end users can fix the failure
mode without reading release notes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    format_package_doc,
    format_packages_list,
    format_references,
)
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.extraction.reference_kind import ReferenceKind

if TYPE_CHECKING:
    # Avoid hard imports — these services may be absent pre-#5 / pre-#5b.
    from pydocs_mcp.application.reference_service import ReferenceService
    from pydocs_mcp.application.tree_service import TreeService


@dataclass(frozen=True, slots=True)
class LookupService:
    """Routes lookup targets to the right backing service.

    ``tree_svc`` and ``ref_svc`` are optional. See spec §6.2 for the
    degraded-mode policy.
    """

    package_lookup: PackageLookup
    tree_svc: "TreeService | None" = None
    ref_svc: "ReferenceService | None" = None

    async def lookup(self, payload: LookupInput) -> str:
        target = payload.target
        show = payload.show
        limit = payload.limit

        # 1. Empty target → list all indexed packages
        if not target:
            packages = await self.package_lookup.list_packages()
            return format_packages_list(packages)

        parts = target.split(".")
        package = parts[0]

        # 2. Single segment → package overview
        if len(parts) == 1:
            doc = await self.package_lookup.get_package_doc(package)
            if doc is None:
                raise NotFoundError(f"package '{package}' not indexed")
            return format_package_doc(doc)

        # 3. Resolve longest module prefix. The return is (module_id, n)
        # where n is the count of INPUT dotted-parts the match consumed —
        # NOT len(module.split(".")), because doc/notebook module ids
        # carry a synthetic suffix segment (``.md`` / ``.ipynb``) that
        # was never in the user's input. Slicing by the synthetic length
        # would discard a trailing symbol the user actually typed.
        match = await self._longest_indexed_module(package, parts)
        if match is None:
            raise NotFoundError(
                f"no module matching '{target}' found under '{package}'"
            )
        module, consumed = match
        symbol_path = parts[consumed:]

        # 4. Module-only target
        if not symbol_path:
            return await self._module_lookup(package, module)

        # 5. Symbol lookup — ``limit`` flows down into the reference-graph
        # branches so YAML-tuned ``reference_graph.output.default_limit``
        # caps the rendered row count.
        return await self._symbol_lookup(package, module, target, show, limit)

    async def _module_lookup(self, package: str, module: str) -> str:
        if self.tree_svc is None:
            raise ServiceUnavailableError(
                f"module tree for '{module}' unavailable — enable via sub-PR #5"
            )
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
        if self.tree_svc is None:
            raise ServiceUnavailableError(
                f"symbol tree for '{target}' unavailable — enable via sub-PR #5"
            )
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for '{package}.{module}'")
        node = tree.find_node_by_qualified_name(target)
        if node is None:
            raise NotFoundError(f"'{target}' not found in {module}")

        if show in ("default", "tree"):
            return json.dumps(node.to_pageindex_json(), indent=2)

        # Reference-graph branches share the same gate: ref_svc must be
        # wired. The hoist keeps the YAML-anchored error message in one
        # place — actionable for end users hitting the failure mode.
        if show in ("callers", "callees", "inherits"):
            if show == "inherits" and node.kind != "class":
                raise InvalidArgumentError(
                    f"show='inherits' only applies to CLASS nodes, got {node.kind}"
                )
            if self.ref_svc is None:
                # Point at the YAML config knob so end users can fix
                # the failure mode without consulting release notes.
                raise ServiceUnavailableError(
                    "reference graph not configured "
                    "(check reference_graph.capture.enabled in YAML config)"
                )

            if show == "callers":
                rows = await self.ref_svc.callers(package, node.node_id)
            elif show == "callees":
                rows = await self.ref_svc.callees(package, node.node_id)
            else:  # inherits
                rows = await self.ref_svc.find_by_name(
                    node.node_id, kind=ReferenceKind.INHERITS,
                )

            # Cap before render — the service may return more than
            # ``limit`` rows (cross-package fan-in is unbounded). We do
            # the slice here so format_references receives the same
            # bound that we'll surface to the user.
            if len(rows) > limit:
                rows = rows[:limit]
            return format_references(
                rows, target=target, show=show, limit=limit,
            )

        raise InvalidArgumentError(f"unknown show value: {show}")

    # Module-id variants we try for each dotted-prefix candidate. The bare
    # variant matches Python modules (``pkg.foo`` for ``pkg/foo.py``); the
    # suffixed variants match doc/notebook trees whose ids preserve the
    # extension to avoid PK collision with sibling .py files (sub-PR #5 F20).
    # ``lookup("pkg.foo")`` is the natural user query; without this fallback
    # markdown/notebook content would be unreachable unless the user knew
    # to type the ``.md`` suffix.
    _MODULE_ID_VARIANTS: tuple[str, ...] = ("", ".md", ".ipynb")

    async def _longest_indexed_module(
        self, package: str, parts: list[str]
    ) -> tuple[str, int] | None:
        """Walk longest-prefix-first; return ``(module_id, n)`` where n is
        the count of input parts consumed by the match, or ``None``.

        For each dotted-prefix candidate we probe three module-id shapes:
        the bare name (``pkg.foo`` — Python module), and the two doc/notebook
        suffixed forms (``pkg.foo.md`` / ``pkg.foo.ipynb``). The bare form
        is tried first so a real Python module wins over a sibling doc file
        with the same stem, matching the user's most likely intent. The
        suffix is ours, never the user's — so ``consumed`` reflects the
        prefix length ``i`` regardless of which variant matched (the caller
        uses ``consumed`` to slice ``parts`` for the symbol-path remainder).

        Prefers ``tree_svc.exists`` when wired; falls back to
        ``PackageLookup.find_module`` otherwise (spec §6.4).

        ``exists`` is a cheap row probe — no JSON parse — so the dotted-
        prefix walk doesn't pay full deserialization for each candidate.
        ``_module_lookup`` / ``_symbol_lookup`` reload the winner via
        ``get_tree``; the duplicate fetch is one extra parse, not N.
        """
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            for suffix in self._MODULE_ID_VARIANTS:
                variant = candidate + suffix
                if self.tree_svc is not None:
                    if await self.tree_svc.exists(package, variant):
                        return (variant, i)
                if await self.package_lookup.find_module(package, variant):
                    return (variant, i)
        return None
