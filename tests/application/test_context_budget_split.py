"""get_context — proportional per-card budget split across multi targets (Task 7).

A single ``get_context`` request batches N targets under ONE shared token
budget (``context_token_budget``). The router resolves each target's forward
dependency closure, then splits the global budget proportionally to closure
size (with a 10% floor per card) and renders each card at its own share.

These tests pin the observable contract:

1. A large closure gets a visibly larger card than a small one (proportional).
2. A solo target gets at least as much room as the same target sharing the
   budget with a sibling (single target = full budget).
3. Every card gets a non-trivial floor share regardless of size skew.

The router is wired over a REAL ``LookupService`` (not the ``FakeLookup``
stub) so the two-phase split actually runs; ``ref_svc.context`` returns a
closure whose size is keyed by target so proportionality is observable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_inputs import ContextInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.reference_service import ContextNode
from pydocs_mcp.application.tool_router import ToolRouter

from ._router_fakes import (
    FakeApi,
    FakeDocs,
    FakeOverview,
    FakeSymbolSource,
    make_envelope,
    make_project,
)


def _ctx_node(qname: str) -> ContextNode:
    # A fat source body so the token budget is the binding constraint: with a
    # small per-card share the skeleton renderer truncates, so a smaller budget
    # yields a shorter card. This makes the proportional split observable rather
    # than trivially satisfied by closure size alone.
    body = "\n".join(f"    line_{i} = {i}" for i in range(40))
    return ContextNode(
        qualified_name=qname,
        hop=0,
        pagerank=0.0,
        in_degree=0,
        source_text=f"def {qname.replace('.', '_')}():\n{body}\n    return 1\n",
    )


# Closure sizes keyed by target: A resolves to a 9-node closure, B to 1 node.
_CLOSURES: dict[str, tuple[ContextNode, ...]] = {
    "pkg.A": tuple(_ctx_node(f"pkg.A.dep{i}") for i in range(9)),
    "pkg.B": (_ctx_node("pkg.B.dep0"),),
}


def _tree_svc() -> MagicMock:
    """Resolves module ``pkg`` (single-segment module) so ``pkg.A`` / ``pkg.B``
    parse to symbol ``A`` / ``B`` inside module ``pkg``; every node's
    ``node_id`` is its qualified name (which keys the closure)."""
    svc = MagicMock()

    async def _exists(package: str, module: str) -> bool:
        return module == "pkg"

    async def _get_tree(package: str, module: str):
        if module != "pkg":
            return None

        def _find(qname: str):
            node = MagicMock()
            node.node_id = qname
            node.kind = "function"
            return node

        tree = MagicMock()
        tree.find_node_by_qualified_name = MagicMock(side_effect=_find)
        return tree

    svc.exists = _exists
    svc.get_tree = _get_tree
    return svc


def _pkg_lookup() -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=())
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


def _lookup_service(*, token_budget: int) -> LookupService:
    ref_svc = MagicMock()

    async def _context(package: str, node_id: str, *, max_depth: int, limit: int):
        return _CLOSURES[node_id]

    ref_svc.context = _context
    return LookupService(
        package_lookup=_pkg_lookup(),
        tree_svc=_tree_svc(),
        ref_svc=ref_svc,
        context_token_budget=token_budget,
    )


def _router(*, token_budget: int = 1000) -> ToolRouter:
    services = (
        ProjectServices(
            project=make_project(),
            docs=FakeDocs(),
            api=FakeApi(),
            lookup=_lookup_service(token_budget=token_budget),
            symbol_source=FakeSymbolSource(),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def test_budget_split_proportional_to_closure_size() -> None:
    # target A resolves to a 9-node closure, target B to a 1-node closure;
    # the global budget splits proportionally so A's card is visibly longer.
    router = _router(token_budget=1000)
    out = asyncio.run(router.get_context(ContextInput(targets=["pkg.A", "pkg.B"])))
    card_a, card_b = out.split("# Context for `pkg.B`")
    assert len(card_a) > 3 * len(card_b)


def test_single_target_uses_full_budget() -> None:
    router = _router(token_budget=1000)
    solo = asyncio.run(router.get_context(ContextInput(targets=["pkg.A"])))
    pair = asyncio.run(router.get_context(ContextInput(targets=["pkg.A", "pkg.B"])))
    # A's card when solo (full budget) is at least as long as when it shares.
    assert len(solo.split("# Context for")[1]) >= len(pair.split("# Context for")[1])


def test_minimum_share_floor() -> None:
    # Even against a 9:1 size skew, B's card gets a non-trivial floor share and
    # still renders its focus block (its own H1 + a node signature block).
    router = _router(token_budget=1000)
    out = asyncio.run(router.get_context(ContextInput(targets=["pkg.A", "pkg.B"])))
    _, card_b = out.split("# Context for `pkg.B`")
    assert "pkg.B.dep0" in card_b
