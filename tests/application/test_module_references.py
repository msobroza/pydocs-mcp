"""Pure unit tests for ``application.module_references`` (spec §1, bug 1).

Covers the seed set (AC1.9 cap), the importer merge (AC1.2), the exact
multi-seed impact merge against a brute-force global ranking (AC1.3) and the
two module-target rejection messages (AC1.4 / E1).

No service, no UoW: every helper here is either pure or takes its collaborator
as a parameter, so the fakes are local dataclass-shaped doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.module_references import (
    merge_impact,
    module_impact_rows,
    module_importer_rows,
    module_internal_qnames,
    module_seed_ids,
    reject_module_show,
)
from pydocs_mcp.application.reference_service import ImpactNode
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference

# ── tree builders ──────────────────────────────────────────────────────────


def _node(qname: str, kind: NodeKind, children: tuple[DocumentNode, ...] = ()) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=9,
        text="",
        content_hash="h",
        children=children,
    )


def _module_tree() -> DocumentNode:
    """``M`` with an import block, a class (one method), a function and an example."""
    load = _node("M.Loader.load", NodeKind.METHOD)
    return _node(
        "M",
        NodeKind.MODULE,
        children=(
            _node("M.__imports__", NodeKind.IMPORT_BLOCK),
            _node("M.Loader", NodeKind.CLASS, children=(load,)),
            _node("M.parse", NodeKind.FUNCTION),
            _node("M.example", NodeKind.CODE_EXAMPLE),
        ),
    )


# ── AC1.9 / seeds ──────────────────────────────────────────────────────────


def test_seeds_are_the_module_root_then_its_class_and_function_children() -> None:
    seeds = module_seed_ids(_module_tree(), cap=32)
    assert seeds.ids == ("M", "M.Loader", "M.parse")
    assert seeds.member_total == 2
    assert seeds.unsearched_members == 0


def test_seeds_skip_import_blocks_code_examples_and_nested_methods() -> None:
    ids = module_seed_ids(_module_tree(), cap=32).ids
    assert "M.__imports__" not in ids
    assert "M.example" not in ids
    assert "M.Loader.load" not in ids


def test_seeds_are_capped_while_the_member_total_stays_uncapped() -> None:
    """AC1.9: exactly ``cap`` seeds are searched; the total drives the entry."""
    wide = _node(
        "M",
        NodeKind.MODULE,
        children=tuple(_node(f"M.f{i}", NodeKind.FUNCTION) for i in range(10)),
    )
    seeds = module_seed_ids(wide, cap=4)
    assert seeds.ids == ("M", "M.f0", "M.f1", "M.f2")
    assert seeds.member_total == 10
    assert seeds.unsearched_members == 7


def test_internal_qnames_cover_the_whole_module_tree() -> None:
    assert module_internal_qnames(_module_tree()) == frozenset(
        {"M", "M.__imports__", "M.Loader", "M.Loader.load", "M.parse", "M.example"}
    )


# ── AC1.2 / importer rows ──────────────────────────────────────────────────


def _ref(frm: str, to: str, kind: ReferenceKind) -> NodeReference:
    return NodeReference(
        from_package="pkg",
        from_node_id=frm,
        to_name=to,
        to_node_id=to,
        kind=kind,
    )


@dataclass
class _FakeCallers:
    """``ReferenceNavigator.callers`` double: one canned answer per qname."""

    by_qname: dict[str, tuple[NodeReference, ...]]

    async def callers(self, package: str, node_qname: str, /) -> tuple[NodeReference, ...]:
        return self.by_qname.get(node_qname, ())


async def test_importer_rows_keep_every_kind_on_the_module_id() -> None:
    svc = _FakeCallers(
        {
            "M": (
                _ref("other", "M", ReferenceKind.IMPORTS),
                _ref("decision:d1", "M", ReferenceKind.GOVERNS),
            )
        }
    )
    rows = await module_importer_rows(svc, "pkg", ("M",))
    assert [r.kind for r in rows] == [ReferenceKind.IMPORTS, ReferenceKind.GOVERNS]


async def test_importer_rows_keep_only_imports_edges_into_children() -> None:
    """A CALLS edge into a member is ``get_references`` on the member's job."""
    svc = _FakeCallers(
        {
            "M.parse": (
                _ref("other.run", "M.parse", ReferenceKind.CALLS),
                _ref("other", "M.parse", ReferenceKind.IMPORTS),
                _ref("other.Sub", "M.parse", ReferenceKind.INHERITS),
            )
        }
    )
    rows = await module_importer_rows(svc, "pkg", ("M", "M.parse"))
    assert [(r.from_node_id, r.kind) for r in rows] == [("other", ReferenceKind.IMPORTS)]


async def test_importer_rows_order_module_first_and_deduplicate() -> None:
    shared = _ref("other", "M", ReferenceKind.IMPORTS)
    svc = _FakeCallers(
        {
            "M": (shared,),
            "M.Loader": (replace(shared, to_name="M", to_node_id="M"),),
            "M.parse": (_ref("late", "M.parse", ReferenceKind.IMPORTS),),
        }
    )
    rows = await module_importer_rows(svc, "pkg", ("M", "M.Loader", "M.parse"))
    assert [(r.from_node_id, r.to_node_id) for r in rows] == [("other", "M"), ("late", "M.parse")]


# ── AC1.3 / impact merge ───────────────────────────────────────────────────

# Reverse adjacency: ``callers_of[q]`` are the direct callers of ``q``. Three
# internal members call ``M.parse``, so a per-seed slice at ``limit`` would
# spend every slot on internals and lose the external ``ext.a`` — the exact
# merge asks each seed for ``limit + len(internal)`` rows instead.
_CALLERS_OF: dict[str, tuple[str, ...]] = {
    "M": ("ext.d",),
    "M.Loader": ("ext.b",),
    "M.parse": ("M.Loader.load", "M.Loader.save", "M.Loader.dump", "ext.a"),
    "M.Loader.load": ("ext.e",),
    "ext.b": ("ext.c",),
}
_PAGERANK: dict[str, float] = {
    "M.Loader.load": 0.8,
    "M.Loader.save": 0.8,
    "M.Loader.dump": 0.8,
    "ext.a": 0.1,
    "ext.b": 0.9,
    "ext.c": 0.2,
    "ext.d": 0.5,
    "ext.e": 0.3,
}
_INTERNAL = frozenset(
    {"M", "M.Loader", "M.Loader.load", "M.Loader.save", "M.Loader.dump", "M.parse"}
)
_SEEDS = ("M", "M.Loader", "M.parse")


def _walk(seed: str, max_depth: int) -> dict[str, int]:
    """Min-hop reverse BFS — the shape ``find_transitive_callers`` returns."""
    hops: dict[str, int] = {}
    frontier = [seed]
    for hop in range(1, max_depth + 1):
        nxt: list[str] = []
        for node in frontier:
            for caller in _CALLERS_OF.get(node, ()):
                if caller not in hops and caller != seed:
                    hops[caller] = hop
                    nxt.append(caller)
        frontier = nxt
    return hops


def _rank(nodes: list[ImpactNode]) -> list[ImpactNode]:
    nodes.sort(key=lambda n: (n.hop, -n.pagerank, -n.in_degree, n.qualified_name))
    return nodes


def _impact_nodes(hops: dict[str, int]) -> list[ImpactNode]:
    return _rank(
        [
            ImpactNode(
                qualified_name=q,
                hop=hop,
                pagerank=_PAGERANK[q],
                in_degree=len(_CALLERS_OF.get(q, ())),
                has_scores=True,
            )
            for q, hop in hops.items()
        ]
    )


@dataclass
class _FakeImpactNavigator:
    """``CrossNavigator.impact`` double over ``_CALLERS_OF``; records its limits."""

    limits: list[int]

    async def impact(
        self,
        service: object,
        package: str,
        qname: str,
        /,
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[ImpactNode, ...]:
        self.limits.append(limit)
        return tuple(_impact_nodes(_walk(qname, max_depth))[:limit])


def _brute_force_global(limit: int, max_depth: int) -> tuple[ImpactNode, ...]:
    """Rank every seed's reachable set as ONE graph, min hop wins."""
    best: dict[str, int] = {}
    for seed in _SEEDS:
        for qname, hop in _walk(seed, max_depth).items():
            best[qname] = min(hop, best.get(qname, hop))
    external = {q: hop for q, hop in best.items() if q not in _INTERNAL}
    return tuple(_impact_nodes(external)[:limit])


async def test_module_impact_rows_equal_a_brute_force_global_ranking() -> None:
    nav = _FakeImpactNavigator(limits=[])
    rows = await module_impact_rows(
        nav,
        object(),
        "pkg",
        "M",
        _SEEDS,
        _INTERNAL,
        max_depth=3,
        limit=3,
    )
    assert rows == _brute_force_global(limit=3, max_depth=3)


async def test_module_impact_rows_ask_each_seed_for_limit_plus_internal() -> None:
    """The per-seed over-fetch is what makes the merge exact (§1 proof)."""
    nav = _FakeImpactNavigator(limits=[])
    await module_impact_rows(nav, object(), "pkg", "M", _SEEDS, _INTERNAL, max_depth=3, limit=3)
    assert nav.limits == [3 + len(_INTERNAL)] * len(_SEEDS)


async def test_exact_merge_beats_a_naive_per_seed_slice() -> None:
    """Non-vacuity guard: slicing each seed at ``limit`` loses ``ext.a``."""
    naive = merge_impact(
        [tuple(_impact_nodes(_walk(seed, 3))[:3]) for seed in _SEEDS],
        _INTERNAL,
        "M",
        3,
    )
    exact = _brute_force_global(limit=3, max_depth=3)
    assert "ext.a" in [n.qualified_name for n in exact]
    assert "ext.a" not in [n.qualified_name for n in naive]


def test_merge_impact_drops_internals_and_module_prefixed_rows() -> None:
    rows = (
        ImpactNode("M.Loader.load", 1, 0.8, 1, True),
        ImpactNode("M.later.helper", 1, 0.7, 1, True),
        ImpactNode("ext.a", 1, 0.1, 1, True),
    )
    merged = merge_impact([rows], _INTERNAL, "M", 10)
    assert [n.qualified_name for n in merged] == ["ext.a"]


def test_merge_impact_keeps_the_minimum_hop_per_identity() -> None:
    far = ImpactNode("ext.a", 3, 0.1, 1, True)
    near = ImpactNode("ext.a", 1, 0.1, 1, True)
    assert merge_impact([(far,), (near,)], _INTERNAL, "M", 10) == (near,)


def test_merge_impact_separates_identical_names_from_different_projects() -> None:
    here = ImpactNode("ext.a", 1, 0.1, 1, True, project="")
    there = ImpactNode("ext.a", 2, 0.9, 1, True, project="other")
    merged = merge_impact([(here,), (there,)], _INTERNAL, "M", 10)
    assert {(n.qualified_name, n.project) for n in merged} == {("ext.a", ""), ("ext.a", "other")}


# ── AC1.4 / E1 messages ────────────────────────────────────────────────────


@pytest.mark.parametrize("show", ["inherits", "context"])
def test_reject_module_show_raises_invalid_argument_naming_the_target(show: str) -> None:
    with pytest.raises(InvalidArgumentError) as exc:
        reject_module_show("pkg.mod", show)
    text = str(exc.value)
    assert "pkg.mod" in text
    assert "module" in text
    assert "show=" not in text
    assert "CLASS nodes" not in text


@pytest.mark.parametrize("show", ["callers", "callees", "impact", "governed_by", "tree"])
def test_reject_module_show_passes_every_other_direction(show: str) -> None:
    reject_module_show("pkg.mod", show)
