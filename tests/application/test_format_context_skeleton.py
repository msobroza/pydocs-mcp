"""Skeleton rendering for context cards (§D6).

Skeleton mode renders every closure node's signature line (plus its first
docstring line when present) and appends FULL bodies only to the most-central
nodes, ranked by ``(pagerank if any node has pagerank else in_degree, -hop)``
until a body budget (``body_ratio * token_budget * _CHARS_PER_TOKEN`` chars) is
spent. ``render="full"`` stays byte-identical to the legacy hop-graded tiering.
"""

from __future__ import annotations

from pydocs_mcp.pointer_table import PointerTableConfig
from pydocs_mcp.application.formatting import format_context
from pydocs_mcp.application.reference_service import ContextNode


# The shipped pointer table — what every composition root threads into
# these renderers, so a test sees the follow-ups a deployment renders.
_POINTER_TABLE = PointerTableConfig()


def _node(qname, hop, pagerank=0.0, in_degree=0, body="def f():\n    return 1\n"):
    return ContextNode(
        qualified_name=qname,
        hop=hop,
        pagerank=pagerank,
        in_degree=in_degree,
        source_text=body,
    )


def test_skeleton_gives_full_bodies_to_most_central_only() -> None:
    nodes = (
        _node("seed", 0, pagerank=0.9),
        _node("hot", 1, pagerank=0.8, body="def hot():\n    return 'big'\n" * 3),
        _node("cold", 1, pagerank=0.1, body="def cold():\n    return 'big'\n" * 3),
    )
    out = format_context(
        nodes,
        target="seed",
        token_budget=200,
        render="skeleton",
        body_ratio=0.5,
        pointers=_POINTER_TABLE,
    )
    assert "return 'big'" in out.split("cold")[0]  # hot's body rendered
    assert "def cold():" in out  # cold: signature line only
    assert out.count("return 'big'") < 6  # cold's body NOT rendered


def test_in_degree_breaks_ties_when_pagerank_absent() -> None:
    # Exactly ONE non-focus node earns a full body — the tie-break between a
    # (in_degree=9) and b (in_degree=1) decides which. Asserting a's body renders before b's block
    # (and b stays signature-only) tests the in_degree fallback path in
    # _rank_context_nodes; flipping the degrees flips which node gets the body,
    # so this assertion actually exercises the ranking rather than fixed lead
    # text. (The bare `out.index("a")` anchored on the 'a' in "max depth" and
    # was vacuous.)
    # body_ratio=0.5 caps max_bodies at 2 (ceil(0.5*3)): the focus takes the
    # first slot (turn-efficiency step 4c), so the second decides a vs b.
    nodes = (_node("seed", 0), _node("a", 1, in_degree=9), _node("b", 1, in_degree=1))
    out = format_context(
        nodes,
        target="seed",
        token_budget=200,
        render="skeleton",
        body_ratio=0.5,
        pointers=_POINTER_TABLE,
    )
    assert "return 1" in out.split("## `b`")[0]  # a's body rendered before b's block
    assert "return 1" not in out.split("## `b`")[1]  # b: signature-only, no body


def test_render_full_preserves_hop_graded_bytes() -> None:
    nodes = (_node("seed", 0), _node("x", 1))
    legacy = format_context(nodes, target="seed", token_budget=500, pointers=_POINTER_TABLE)
    explicit = format_context(
        nodes, target="seed", token_budget=500, render="full", pointers=_POINTER_TABLE
    )
    assert legacy == explicit


# ── body slots go to nodes that have a body (turn-efficiency step 4c) ──────
#
# A closure node with no indexed source — a builtin, an unresolved import —
# used to cost 0 chars, always fit, and eat a body slot, so the focus symbol
# itself could be left with only its signature.

_SEED = "def seed():\n    return 1\n"


def _skeleton(nodes: tuple[ContextNode, ...], *, body_ratio: float, budget: int = 200) -> str:
    return format_context(
        nodes,
        target="seed",
        token_budget=budget,
        render="skeleton",
        body_ratio=body_ratio,
        pointers=_POINTER_TABLE,
    )


def _block(out: str, qname: str) -> str:
    """The skeleton block ``qname`` renders, up to the next block."""
    return out.split(f"## `{qname}`")[1].split("\n## ")[0]


def test_an_empty_source_node_never_takes_a_body_slot() -> None:
    # Two slots over the three nodes that have a body: the focus, then the most
    # central sourced node — never the more central builtin.
    nodes = (
        _node("seed", 0, pagerank=0.9, body=_SEED),
        _node("builtins.len", 1, pagerank=0.99, body=""),
        _node("a", 1, pagerank=0.5, body="def a():\n    return 'a'\n"),
        _node("b", 1, pagerank=0.1, body="def b():\n    return 'b'\n"),
    )
    out = _skeleton(nodes, body_ratio=0.5)
    assert "return 'a'" in _block(out, "a")
    assert "return 'b'" not in _block(out, "b")


def test_an_empty_source_block_says_so_and_offers_no_source_pointer() -> None:
    # Its source call would only raise "no indexed source" — whether or not
    # the node ranks high enough that it once would have taken a slot.
    for pagerank in (0.99, 0.0):
        nodes = (
            _node("seed", 0, pagerank=0.5, body=_SEED),
            _node("builtins.len", 1, pagerank=pagerank, body=""),
        )
        out = _skeleton(nodes, body_ratio=0.5)
        assert _block(out, "builtins.len") == "\n\n```python\n# (source unavailable)\n```\n"


def test_max_bodies_counts_only_nodes_with_a_body() -> None:
    # ceil(0.5 * 3 sourced nodes) = 2 slots, not ceil(0.5 * 6 nodes) = 3.
    nodes = (
        _node("seed", 0, pagerank=0.9, body=_SEED),
        *(_node(f"builtins.b{i}", 1, body="") for i in range(3)),
        _node("a", 1, pagerank=0.5, body="def a():\n    return 'a'\n"),
        _node("b", 1, pagerank=0.4, body="def b():\n    return 'b'\n"),
    )
    out = _skeleton(nodes, body_ratio=0.5)
    assert "return 'a'" in _block(out, "a")
    assert "return 'b'" not in _block(out, "b")


def test_the_focus_node_is_admitted_first_when_it_fits() -> None:
    # One slot (ceil(0.3 * 3)); the focus is the least central node, yet it
    # is what the agent asked about, so its body comes first.
    nodes = (
        _node("seed", 0, pagerank=0.0, body=_SEED),
        _node("a", 1, pagerank=0.9, body="def a():\n    return 'a'\n"),
        _node("b", 1, pagerank=0.8, body="def b():\n    return 'b'\n"),
    )
    out = _skeleton(nodes, body_ratio=0.3)
    assert "return 1" in _block(out, "seed")
    assert "return 'a'" not in _block(out, "a")


def test_a_focus_too_big_for_the_body_budget_leaves_its_slot_to_the_ranked_rest() -> None:
    huge = "def seed():\n" + "    x = 1\n" * 200
    nodes = (
        _node("seed", 0, pagerank=0.0, body=huge),
        _node("a", 1, pagerank=0.9, body="def a():\n    return 'a'\n"),
    )
    out = _skeleton(nodes, body_ratio=0.5)
    assert "return 'a'" in _block(out, "a")
    assert "x = 1" not in _block(out, "seed")


def test_render_full_is_byte_identical_with_an_empty_source_node() -> None:
    nodes = (
        _node("seed", 0, pagerank=0.1, body=_SEED),
        _node("builtins.len", 1, pagerank=0.99, body=""),
        _node("a", 1, pagerank=0.5, body="def a():\n    return 2\n"),
        _node("b", 2, pagerank=0.2, body=""),
    )
    out = format_context(
        nodes, target="seed", token_budget=500, render="full", pointers=_POINTER_TABLE
    )
    assert out == (
        "# Context for `seed` — its dependency closure\n"
        "4 symbols in the closure (max depth 2). Graded fidelity: focus = full source, "
        "ring = signature, rest = outline.\n"
        "\n## Focus — `seed`\n\n```python\ndef seed():\n    return 1\n\n```\n"
        "\n## `builtins.len` — signature\n\n```python\n# `builtins.len`\n```\n"
        "\n## `a` — signature\n\n```python\ndef a():\n```\n"
        "- `b` (hop 2)\n"
    )
