"""Null Object impls for optional service deps (I9 + S15-style).

When a deployment doesn't index trees or references, the composition
root wires these Null impls so :class:`LookupService` can drop its
``if X is None:`` soft-dependency guards.  The Null impls preserve
the user-visible error contract: they raise the same
:class:`ServiceUnavailableError` with the YAML-anchored message that
the pre-refactor ``is None`` checks raised, so MCP clients see no
behavioral change when reaching ``lookup(show="callers")`` against
a deployment that hasn't enabled the reference graph.

Why raise from the Null impls (not "return [] silently"):

1. **Backward compatibility.**  The pre-refactor LookupService raised
   ``ServiceUnavailableError`` with a YAML-anchored message
   (``reference_graph.capture.enabled``) when ``ref_svc=None``.  End
   users hit that error and edit YAML to fix it.  Silently returning
   empty rows would render a misleading "no callers found" instead
   of pointing at the config knob — that's an information loss.

2. **The dispatcher stays branch-free.**  ``LookupService._symbol_lookup``
   no longer has an ``if self.ref_svc is None: raise ...`` branch —
   the dispatch table calls into ``ref_svc`` uniformly and either
   gets real rows OR a ``ServiceUnavailableError`` propagated up.
   Same uniform shape, no marker checks.

Polymorphic substitutability: these classes structurally satisfy
:class:`TreeService` / :class:`ReferenceService` so type-hinted
parameters accept them without a Protocol declaration.  Mypy /
pyright see the same method signatures; runtime sees the same
``async def`` returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError

# Single source of truth for the YAML-anchored failure-mode pointer.
# End users hitting this error must be able to fix it by editing
# config; the message names the exact YAML key
# (``reference_graph.capture.enabled``) so they don't need to read
# release notes or grep the docs.
_REFERENCE_GRAPH_DISABLED_MSG = (
    "reference graph not configured (check reference_graph.capture.enabled in YAML config)"
)
# Parallel YAML-anchored hint for the tree-index failure mode.  Document
# trees are built by the default ingestion pipeline today (no opt-out
# switch yet), so the actionable hint points at re-indexing rather than
# a YAML toggle.  Mirrors ``_REFERENCE_GRAPH_DISABLED_MSG``: give the
# user a concrete next step, not just "unavailable".
_TREE_INDEX_DISABLED_MSG = (
    "module tree unavailable for this deployment "
    "(NullTreeService is wired; document trees are not indexed). "
    "Re-index with the default ingestion pipeline (which builds trees) "
    "to enable tree-based lookups."
)


@dataclass(frozen=True, slots=True)
class NullTreeService:
    """Tree-service stand-in for deployments that don't index trees.

    - ``get_tree`` raises ``ServiceUnavailableError`` so the dispatcher
      preserves the pre-refactor user-facing message contract.
    - ``exists`` returns ``False`` (NOT raise) so
      ``LookupService._longest_indexed_module`` falls through to
      ``PackageLookup.find_module`` cleanly — the no-tree-index case
      degrades gracefully to module-name-only resolution rather than
      crashing the entire dispatch path.
    - ``list_package_modules`` returns an empty dict so callers that
      iterate per-package can short-circuit naturally.
    """

    async def get_tree(self, package: str, module: str):
        raise ServiceUnavailableError(_TREE_INDEX_DISABLED_MSG)

    async def exists(self, package: str, module: str) -> bool:
        # Non-raising on purpose — let _longest_indexed_module's
        # PackageLookup.find_module fallback path run unchanged.
        return False

    async def list_package_modules(self, package: str) -> dict[str, object]:
        return {}


@dataclass(frozen=True, slots=True)
class NullReferenceService:
    """Reference-service stand-in for deployments without the reference graph.

    Every method raises ``ServiceUnavailableError`` so the
    user-visible failure mode (``lookup(show="callers")`` against an
    un-indexed deployment) points squarely at the YAML knob.  Returning
    empty rows would silently mislead the user into thinking no
    callers exist; raising forces them to fix the config.
    """

    async def callers(self, *_args, **_kwargs):
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)

    async def callees(self, *_args, **_kwargs):
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)

    async def find_by_name(self, *_args, **_kwargs):
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)

    async def impact(self, *_args, **_kwargs):
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)

    async def context(self, *_args, **_kwargs):
        raise ServiceUnavailableError(_REFERENCE_GRAPH_DISABLED_MSG)


__all__ = (
    "NullReferenceService",
    "NullTreeService",
)
