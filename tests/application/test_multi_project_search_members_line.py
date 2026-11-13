"""``search.output.members_line`` through the search router (issue #393).

Both paths render the line — one bundle (``render_single_search``) and the
union of several — from each hit's own bundle's trees, under the card's child
cap, and only the TEXT moves: ``items`` and the extras are the knob-off ones.
"""

from __future__ import annotations

from dataclasses import replace

from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import MultiProjectSearch, ProjectServices
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkFilterField, ModuleMember

from ._router_fakes import (
    CountingTreeService,
    FakeLookup,
    FakeRankedApi,
    FakeRankedDocs,
    make_service,
)


def _node(
    qname: str, kind: NodeKind, span: tuple[int, int], *children: DocumentNode
) -> DocumentNode:
    path = qname.split(".")[0] + "/" + qname.split(".")[1] + ".py"
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path=path,
        start_line=span[0],
        end_line=span[1],
        text="",
        content_hash=f"h-{qname}",
        children=children,
    )


def _cart_tree() -> DocumentNode:
    cart = _node(
        "shop.cart.Cart",
        NodeKind.CLASS,
        (4, 15),
        _node("shop.cart.Cart.add", NodeKind.METHOD, (7, 8)),
        _node("shop.cart.Cart.remove", NodeKind.METHOD, (10, 11)),
        _node("shop.cart.Cart.total", NodeKind.METHOD, (13, 15)),
    )
    checkout = _node("shop.cart.checkout", NodeKind.FUNCTION, (18, 20))
    return _node("shop.cart", NodeKind.MODULE, (1, 20), cart, checkout)


def _book_tree() -> DocumentNode:
    book = _node(
        "ledger.book.Book",
        NodeKind.CLASS,
        (4, 8),
        _node("ledger.book.Book.post", NodeKind.METHOD, (7, 8)),
    )
    return _node("ledger.book", NodeKind.MODULE, (1, 8), book)


def _hit(qname: str, module: str, span: tuple[int, int], score: float) -> Chunk:
    return Chunk(
        text=f"body of {qname}",
        relevance=score,
        metadata={
            ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME,
            ChunkFilterField.MODULE.value: module,
            ChunkFilterField.TITLE.value: qname,
            ChunkFilterField.SOURCE_PATH.value: module.replace(".", "/") + ".py",
            ChunkFilterField.START_LINE.value: span[0],
            ChunkFilterField.END_LINE.value: span[1],
            "qualified_name": qname,
        },
    )


_SHOP_HITS = (
    _hit("shop.cart.Cart", "shop.cart", (4, 15), 0.9),
    _hit("shop.cart.checkout", "shop.cart", (18, 20), 0.8),
    _hit("shop.cart", "shop.cart", (1, 20), 0.7),
)


def _shop(
    hits: tuple[Chunk, ...] = _SHOP_HITS, members: tuple[ModuleMember, ...] = ()
) -> ProjectServices:
    trees = {(PROJECT_PACKAGE_NAME, "shop.cart"): _cart_tree()}
    return replace(
        make_service("shop"),
        docs=FakeRankedDocs(hits),
        api=FakeRankedApi(members),
        lookup=FakeLookup(tree_svc=CountingTreeService(trees)),
    )


def _reads(svc: ProjectServices) -> list[tuple[str, str]]:
    return svc.lookup.tree_svc.reads  # type: ignore[attr-defined, no-any-return]


async def test_with_the_line_on_class_and_module_hits_name_their_members() -> None:
    router = MultiProjectSearch(services=(_shop(),), members_line=True)
    body, _items, _extras = await router._search_body(SearchInput(query="cart", kind="docs"))
    assert "## shop.cart.Cart — shop/cart.py:4-15\nMembers (3): add, remove, total\n" in body
    assert "## shop.cart — shop/cart.py:1-20\nMembers (2): Cart, checkout\n" in body
    assert "## shop.cart.checkout — shop/cart.py:18-20\nbody of shop.cart.checkout\n" in body
    assert body.count("Members (") == 2


_CART_MEMBER = ModuleMember(
    id=7,
    relevance=0.5,
    metadata={"package": PROJECT_PACKAGE_NAME, "module": "shop.cart", "name": "Cart"},
)


async def test_the_line_moves_the_text_only() -> None:
    """``items`` and the extras stay the knob-off ones; the text gains the two lines."""
    payload = SearchInput(query="cart")
    off = await MultiProjectSearch(services=(_shop(members=(_CART_MEMBER,)),))._search_body(payload)
    on = await MultiProjectSearch(
        services=(_shop(members=(_CART_MEMBER,)),), members_line=True
    )._search_body(payload)
    assert on[1:] == off[1:]
    removed = on[0].replace("Members (3): add, remove, total\n", "", 1)
    assert removed.replace("Members (2): Cart, checkout\n", "", 1) == off[0]


async def test_with_the_line_off_a_chunk_hit_reads_no_tree() -> None:
    shop = _shop()
    await MultiProjectSearch(services=(shop,))._search_body(SearchInput(query="x", kind="docs"))
    assert _reads(shop) == []


async def test_kind_any_reads_each_module_once_for_the_lines_and_the_member_spans() -> None:
    shop = _shop(members=(_CART_MEMBER,))
    router = MultiProjectSearch(services=(shop,), members_line=True)
    _body, items, _extras = await router._search_body(SearchInput(query="cart"))
    assert _reads(shop) == [(PROJECT_PACKAGE_NAME, "shop.cart")]
    member_row = next(i for i in items if i["kind"] == "member")
    assert (member_row["path"], member_row["start_line"]) == ("shop/cart.py", 4)


async def test_the_cap_is_the_symbol_cards_child_cap() -> None:
    router = MultiProjectSearch(services=(_shop(),), members_line=True, card_child_cap=2)
    body, _items, _extras = await router._search_body(SearchInput(query="cart", kind="docs"))
    assert "\nMembers (3): add, remove, and 1 more\n" in body
    assert "\nMembers (2): Cart, checkout\n" in body
