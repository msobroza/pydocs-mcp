"""Search-hit facts: the ``Members (N): …`` line of class and module hits (issue #393).

The facts come from the hit's document tree, read through its owning bundle's
tree navigator; the counting fake records every read, which is how the bound of
one tree load per distinct (bundle, package, module) per response is pinned.
"""

from __future__ import annotations

from dataclasses import replace

from pydocs_mcp.application.multi_project_search import ProjectServices
from pydocs_mcp.application.search_hit_facts import (
    SearchHitFacts,
    SearchResponseTrees,
    members_line_facts,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkFilterField

from ._router_fakes import CountingTreeService, FakeLookup, make_service


def _node(qname: str, kind: NodeKind, *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path="shop/cart.py",
        start_line=1,
        end_line=20,
        text=f"text of {qname}",
        content_hash=f"h-{qname}",
        children=children,
    )


def _cart_module() -> DocumentNode:
    cart = _node(
        "shop.cart.Cart",
        NodeKind.CLASS,
        _node("shop.cart.Cart.add", NodeKind.METHOD),
        _node("shop.cart.Cart.remove", NodeKind.METHOD),
        _node("shop.cart.Cart.total", NodeKind.METHOD),
    )
    return _node("shop.cart", NodeKind.MODULE, cart, _node("shop.cart.checkout", NodeKind.FUNCTION))


def _hit(qname: str, module: str = "shop.cart") -> Chunk:
    return Chunk(
        text=f"text of {qname}",
        metadata={
            ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME,
            ChunkFilterField.MODULE.value: module,
            "qualified_name": qname,
        },
    )


def _owner(name: str, trees: dict[tuple[str, str], DocumentNode]) -> ProjectServices:
    return replace(make_service(name), lookup=FakeLookup(tree_svc=CountingTreeService(trees)))


def _shop() -> ProjectServices:
    return _owner("shop", {(PROJECT_PACKAGE_NAME, "shop.cart"): _cart_module()})


async def _facts(hits: list[tuple[ProjectServices, Chunk]], *, child_cap: int = 20):
    return await members_line_facts(hits, SearchResponseTrees(), child_cap=child_cap)


async def test_a_class_hit_names_its_members() -> None:
    shop = _shop()
    facts = await _facts([(shop, _hit("shop.cart.Cart"))])
    assert facts == {0: SearchHitFacts(members_line="Members (3): add, remove, total")}


async def test_a_module_hit_names_its_top_level_members() -> None:
    facts = await _facts([(_shop(), _hit("shop.cart"))])
    assert facts == {0: SearchHitFacts(members_line="Members (2): Cart, checkout")}


async def test_function_and_method_hits_state_nothing() -> None:
    shop = _shop()
    hits = [(shop, _hit("shop.cart.checkout")), (shop, _hit("shop.cart.Cart.total"))]
    assert await _facts(hits) == {}


async def test_the_cap_names_child_cap_members_and_counts_the_rest() -> None:
    facts = await _facts([(_shop(), _hit("shop.cart.Cart"))], child_cap=2)
    assert facts[0].members_line == "Members (3): add, remove, and 1 more"


async def test_a_hit_names_exactly_what_its_symbol_card_names() -> None:
    """One line, two views: the card at ``depth="summary"`` and the search hit."""
    from pydocs_mcp.application.symbol_views import render_symbol_card
    from pydocs_mcp.pointer_table import PointerTableConfig

    cart = _cart_module().children[0]
    card = render_symbol_card(cart, child_cap=2, pointers=PointerTableConfig())
    facts = await _facts([(_shop(), _hit("shop.cart.Cart"))], child_cap=2)
    assert facts[0].members_line in card.text.splitlines()


async def test_facts_are_keyed_by_the_hits_rank() -> None:
    shop = _shop()
    hits = [(shop, _hit("shop.cart.checkout")), (shop, _hit("shop.cart")), (shop, _hit("x.y", ""))]
    assert sorted(await _facts(hits)) == [1]


async def test_each_module_tree_is_read_once_per_response() -> None:
    shop = _shop()
    hits = [
        (shop, _hit("shop.cart.Cart")),
        (shop, _hit("shop.cart.checkout")),
        (shop, _hit("shop.cart")),
        (shop, _hit("shop.cart.Cart.add")),
    ]
    await _facts(hits)
    assert shop.lookup.tree_svc.reads == [(PROJECT_PACKAGE_NAME, "shop.cart")]


async def test_two_bundles_holding_the_same_module_each_read_their_own_tree() -> None:
    """The same (package, module) in two bundles is two trees: neither may answer for the other."""
    shop = _shop()
    other_cart = _node("shop.cart", NodeKind.MODULE, _node("shop.cart.Wallet", NodeKind.CLASS))
    other = _owner("other", {(PROJECT_PACKAGE_NAME, "shop.cart"): other_cart})
    facts = await _facts([(shop, _hit("shop.cart")), (other, _hit("shop.cart"))])
    assert facts[0].members_line == "Members (2): Cart, checkout"
    assert facts[1].members_line == "Members (1): Wallet"
    assert (
        shop.lookup.tree_svc.reads
        == other.lookup.tree_svc.reads
        == [(PROJECT_PACKAGE_NAME, "shop.cart")]
    )


async def test_one_cache_serves_every_read_of_the_response() -> None:
    """A second consumer of the same response's trees (the member rows' spans) reads none again."""
    shop = _shop()
    trees = SearchResponseTrees()
    await members_line_facts([(shop, _hit("shop.cart.Cart"))], trees, child_cap=20)
    assert await trees.tree(shop, PROJECT_PACKAGE_NAME, "shop.cart") == _cart_module()
    assert shop.lookup.tree_svc.reads == [(PROJECT_PACKAGE_NAME, "shop.cart")]


async def test_a_hit_without_a_qualified_name_or_module_reads_no_tree() -> None:
    shop = _shop()
    assert await _facts([(shop, _hit("")), (shop, _hit("shop.cart.Cart", module=""))]) == {}
    assert shop.lookup.tree_svc.reads == []


async def test_a_hit_whose_tree_or_node_is_missing_states_nothing() -> None:
    shop = _shop()
    hits = [(shop, _hit("gone.mod.Thing", module="gone.mod")), (shop, _hit("shop.cart.Gone"))]
    assert await _facts(hits) == {}


async def test_a_deployment_without_a_tree_index_states_nothing() -> None:
    """``NullTreeService`` raises on every read; an advisory line degrades to none."""
    from pydocs_mcp.application.null_services import NullTreeService

    owner = replace(make_service("bare"), lookup=FakeLookup(tree_svc=NullTreeService()))
    assert await _facts([(owner, _hit("shop.cart.Cart"))]) == {}


async def test_a_lookup_without_a_tree_navigator_states_nothing() -> None:
    owner = make_service("bare")  # FakeLookup's tree_svc defaults to None
    assert await _facts([(owner, _hit("shop.cart.Cart"))]) == {}
