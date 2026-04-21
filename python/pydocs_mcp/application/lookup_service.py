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
            return await self._package_lookup(package, show)

        # Module / symbol routing implemented in later tasks.
        raise NotFoundError(
            f"target '{target}' not yet resolvable (module/symbol routing in later task)"
        )

    async def _package_lookup(self, package: str, show: str) -> str:
        doc = await self.package_lookup.get_package_doc(package)
        if doc is None:
            raise NotFoundError(f"package '{package}' not indexed")
        return format_package_doc(doc)
