"""Shared builders for the Members-line tests (issue #393).

Two small projects' document trees — ``shop.cart`` (a class with three methods,
plus a function) and ``ledger.book`` (a class with one method) — and the ranked
chunk hits that stand for their nodes, spans included, so the facts seam and
the search-router seam read the same fixture.
"""

from __future__ import annotations

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkFilterField


def source_path_of(qualified_name: str) -> str:
    """``shop.cart.Cart`` → ``shop/cart.py``: these fixtures' modules are two segments."""
    package, module = qualified_name.split(".")[:2]
    return f"{package}/{module}.py"


def tree_node(
    qualified_name: str, kind: NodeKind, span: tuple[int, int], *children: DocumentNode
) -> DocumentNode:
    return DocumentNode(
        node_id=qualified_name,
        qualified_name=qualified_name,
        title=qualified_name.rsplit(".", 1)[-1],
        kind=kind,
        source_path=source_path_of(qualified_name),
        start_line=span[0],
        end_line=span[1],
        text="",
        content_hash=f"h-{qualified_name}",
        children=children,
    )


def shop_cart_tree() -> DocumentNode:
    cart = tree_node(
        "shop.cart.Cart",
        NodeKind.CLASS,
        (4, 15),
        tree_node("shop.cart.Cart.add", NodeKind.METHOD, (7, 8)),
        tree_node("shop.cart.Cart.remove", NodeKind.METHOD, (10, 11)),
        tree_node("shop.cart.Cart.total", NodeKind.METHOD, (13, 15)),
    )
    checkout = tree_node("shop.cart.checkout", NodeKind.FUNCTION, (18, 20))
    return tree_node("shop.cart", NodeKind.MODULE, (1, 20), cart, checkout)


def ledger_book_tree() -> DocumentNode:
    book = tree_node(
        "ledger.book.Book",
        NodeKind.CLASS,
        (4, 8),
        tree_node("ledger.book.Book.post", NodeKind.METHOD, (7, 8)),
    )
    return tree_node("ledger.book", NodeKind.MODULE, (1, 8), book)


def project_hit(
    qualified_name: str, module: str, span: tuple[int, int] = (1, 1), score: float = 0.5
) -> Chunk:
    """A ranked ``__project__`` chunk row standing for ``qualified_name``'s node."""
    return Chunk(
        text=f"body of {qualified_name}",
        relevance=score,
        metadata={
            ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME,
            ChunkFilterField.MODULE.value: module,
            ChunkFilterField.TITLE.value: qualified_name,
            ChunkFilterField.SOURCE_PATH.value: module.replace(".", "/") + ".py",
            ChunkFilterField.START_LINE.value: span[0],
            ChunkFilterField.END_LINE.value: span[1],
            "qualified_name": qualified_name,
        },
    )
