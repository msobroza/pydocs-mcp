"""LookupService — unified dispatch for the 'lookup' MCP tool (sub-PR #6 §6).

Routes a single ``LookupInput.target`` string (empty / package /
package.module / package.module.symbol) to the right backing service:
``PackageLookupService`` for package metadata, ``DocumentTreeService``
(optional, sub-PR #5) for file structure, ``ReferenceService``
(optional, sub-PR #5b) for the call graph.

Soft dependencies — when ``tree_svc`` or ``ref_svc`` is None, ``show``
modes that need them raise ``ServiceUnavailableError``. ``show="inherits"``
degrades gracefully via ``DocumentNode.extra_metadata['inherits_from']``
and only needs ``tree_svc``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.application.formatting import (
    format_package_doc,
    format_packages_list,
)
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.package_lookup_service import PackageLookupService

if TYPE_CHECKING:
    # Avoid hard imports — these services may be absent pre-#5 / pre-#5b.
    from pydocs_mcp.application.document_tree_service import DocumentTreeService
    from pydocs_mcp.application.reference_service import ReferenceService


@dataclass(frozen=True, slots=True)
class LookupService:
    """Routes lookup targets to the right backing service.

    ``tree_svc`` (sub-PR #5) and ``ref_svc`` (sub-PR #5b) are optional.
    See spec §6.2 for the degraded-mode policy.
    """

    package_lookup: PackageLookupService
    tree_svc: "DocumentTreeService | None" = None
    ref_svc: "ReferenceService | None" = None

    async def lookup(self, payload: LookupInput) -> str:
        target = payload.target
        show = payload.show

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

        # 3. Resolve longest module prefix
        module = await self._longest_indexed_module(package, parts)
        if module is None:
            raise NotFoundError(
                f"no module matching '{target}' found under '{package}'"
            )

        symbol_path = parts[len(module.split(".")):]

        # 4. Module-only target
        if not symbol_path:
            return await self._module_lookup(package, module)

        # 5. Symbol lookup
        return await self._symbol_lookup(package, module, target, show)

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
        self, package: str, module: str, target: str, show: str,
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

        if show in ("callers", "callees"):
            if self.ref_svc is None:
                raise ServiceUnavailableError(
                    "reference graph not indexed — enable via sub-PR #5b"
                )
            fetch = self.ref_svc.callers if show == "callers" else self.ref_svc.callees
            return self._render_refs(await fetch(package, node.node_id))

        if show == "inherits":
            if node.kind != "class":
                raise InvalidArgumentError(
                    f"show='inherits' only applies to CLASS nodes, got {node.kind}"
                )
            inherits = node.extra_metadata.get("inherits_from", [])
            if not inherits:
                return "(no base classes)"
            return "\n".join(f"- {base}" for base in inherits)

        raise InvalidArgumentError(f"unknown show value: {show}")

    @staticmethod
    def _render_refs(refs: Any) -> str:
        if not refs:
            return "(no references)"
        return "\n".join(
            f"- {r.from_node_id} → {r.to_name} ({r.kind})" for r in refs
        )

    async def _longest_indexed_module(
        self, package: str, parts: list[str]
    ) -> str | None:
        """Walk longest-prefix-first; return the longest dotted path that is
        an indexed module. Prefers ``tree_svc.get_tree`` when wired; falls
        back to ``PackageLookupService.find_module`` otherwise (spec §6.4).
        """
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if self.tree_svc is not None:
                tree = await self.tree_svc.get_tree(package, candidate)
                if tree is not None:
                    return candidate
            if await self.package_lookup.find_module(package, candidate):
                return candidate
        return None
