"""ReferenceService — read-side wrapper over the reference graph (spec §8.1).

Follows the CLAUDE.md §"Creating new application services" contract:
single ``uow_factory`` constructor parameter, per-method UoW open/close,
reads only (no ``await uow.commit()``). The ``__aexit__`` safety-net
rollback is a no-op for read-only paths.

#5b ships the service; #5c wires it into ``LookupService.ref_svc`` and
flips ``LookupService._symbol_lookup`` to invoke it for
``show="callers"|"callees"``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class ReferenceService:
    """Reads the cross-node reference graph through a per-call UnitOfWork.

    All three public methods open a fresh UoW, read via ``uow.references``,
    and return tuples (not lists — frozen+hashable contract for the
    rendering layer downstream).

    **2-arg signature note (controller decision C1):** `callers` and
    `callees` take ``(package, target_node_qname)`` to match the existing
    `LookupService._symbol_lookup` call site in
    `tests/application/test_lookup_service.py:357,381`. The `package`
    argument is informational — the underlying `uow.references.find_*`
    calls remain cross-package per spec §6.2 (no package filter). This
    fixes a spec/test inconsistency in §8.1 (spec was 1-arg). Task 20
    amends the spec to match.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def callers(
        self,
        package: str,
        target_node_qname: str,
    ) -> tuple[NodeReference, ...]:
        """Return every ref whose ``to_node_id == target_node_qname``.

        Cross-package per spec §6.2 — the answer to "who calls X" should
        not be filtered by source package. The `package` argument is
        provided for API symmetry with `LookupService._symbol_lookup`
        (which already passes 2 args today) and for downstream rendering
        context. It is NOT used to filter results.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callers(
                target_node_id=target_node_qname,
            )
        return tuple(rows)

    async def callees(
        self,
        package: str,
        from_node_qname: str,
    ) -> tuple[NodeReference, ...]:
        """Return every ref originating from ``from_node_qname``.

        Same rationale as ``callers`` — `package` is informational, the
        storage Protocol is cross-package.
        """
        async with self.uow_factory() as uow:
            rows = await uow.references.find_callees(
                from_node_id=from_node_qname,
            )
        return tuple(rows)

    async def find_by_name(
        self,
        name: str,
        *,
        kind: ReferenceKind | None = None,
    ) -> tuple[NodeReference, ...]:
        """Find every ref whose ``to_name == name`` (queryable for both
        resolved AND unresolved edges — that's the whole point of keeping
        unresolved rows queryable)."""
        async with self.uow_factory() as uow:
            rows = await uow.references.find_by_name(name, kind)
        return tuple(rows)
