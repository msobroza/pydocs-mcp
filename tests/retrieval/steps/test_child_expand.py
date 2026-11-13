"""ChildExpandStep — fill a search's free result slots with the members of its
top class / module hits, never displacing an original hit (#393, overlay-only).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.config import AppConfig, _resolve_pipeline_path
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.child_expand import ChildExpandStep
from pydocs_mcp.retrieval.steps.limit import LimitStep, rows_dropped_by_limit
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_PKG = "pkg"
_MOD = "pkg.mod"
_SHIPPED_PIPELINES = Path(__file__).resolve().parents[3] / "python" / "pydocs_mcp" / "pipelines"


def _load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── builders ──────────────────────────────────────────────────────────────


def _node(qname: str, kind: NodeKind, *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=2,
        text=f"text of {qname}",
        content_hash=f"hash-{qname}",
        children=children,
    )


def _class(qname: str, *method_names: str) -> DocumentNode:
    methods = [_node(f"{qname}.{m}", NodeKind.METHOD) for m in method_names]
    return _node(qname, NodeKind.CLASS, *methods)


def _module(*children: DocumentNode, qname: str = _MOD) -> DocumentNode:
    return _node(qname, NodeKind.MODULE, *children)


def _chunk(
    qname: str,
    relevance: float | None = None,
    *,
    kind: NodeKind = NodeKind.FUNCTION,
    module: str = _MOD,
    package: str = _PKG,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        text=text or f"text of {qname}",
        relevance=relevance,
        metadata={
            "package": package,
            "module": module,
            "qualified_name": qname,
            "kind": kind.value,
        },
    )


def _hit(qname: str, relevance: float, kind: NodeKind = NodeKind.FUNCTION) -> Chunk:
    return _chunk(qname, relevance, kind=kind)


async def _step(
    trees: Sequence[DocumentNode] = (),
    rows: Sequence[Chunk] = (),
    **knobs: object,
) -> ChildExpandStep:
    """A step over fakes holding ``trees`` (package ``pkg``) and chunk ``rows``."""
    tree_store = InMemoryDocumentTreeStore()
    chunk_store = InMemoryChunkStore()
    if trees:
        await tree_store.save_many(list(trees), package=_PKG)
    if rows:
        await chunk_store.upsert(list(rows))
    factory = make_fake_uow_factory(trees=tree_store, chunks=chunk_store)
    return ChildExpandStep(uow_factory=factory, **knobs)  # type: ignore[arg-type]


def _state(items: Sequence[Chunk], *, cap: int | None = 8) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q", max_results=cap),
        candidates=ChunkList(items=tuple(items)),
    )


def _qnames(state: RetrieverState) -> list[str]:
    assert isinstance(state.candidates, ChunkList)
    return [c.metadata["qualified_name"] for c in state.candidates.items]


def _ctx() -> BuildContext:
    return BuildContext(uow_factory=make_fake_uow_factory())


# ── expansion ─────────────────────────────────────────────────────────────


async def test_a_class_hit_s_members_fill_the_free_slots_below_the_original_hits() -> None:
    tree = _module(_class(f"{_MOD}.Router", "add", "remove"))
    rows = [_chunk(f"{_MOD}.Router.add"), _chunk(f"{_MOD}.Router.remove")]
    step = await _step([tree], rows)
    hits = [_hit(f"{_MOD}.Router", 0.8, NodeKind.CLASS), _hit(f"{_MOD}.helper", 0.7)]

    out = await step.run(_state(hits, cap=5))

    assert _qnames(out) == [
        f"{_MOD}.Router",
        f"{_MOD}.helper",
        f"{_MOD}.Router.add",
        f"{_MOD}.Router.remove",
    ]


def _relevance_of(state: RetrieverState, qname: str) -> float | None:
    assert isinstance(state.candidates, ChunkList)
    (chunk,) = [c for c in state.candidates.items if c.metadata["qualified_name"] == qname]
    return chunk.relevance


async def _router_and_helper(helper_relevance: float) -> RetrieverState:
    tree = _module(_class(f"{_MOD}.Router", "add"))
    step = await _step([tree], [_chunk(f"{_MOD}.Router.add")])
    hits = [_hit(f"{_MOD}.Router", 0.8, NodeKind.CLASS), _hit(f"{_MOD}.helper", helper_relevance)]
    return await step.run(_state(hits, cap=4))


async def test_a_query_without_a_cap_fills_up_to_the_limit_step_s_default_of_8() -> None:
    methods = [f"m{i}" for i in range(10)]
    step = await _step(
        [_module(_class(f"{_MOD}.Router", *methods))],
        [_chunk(f"{_MOD}.Router.{m}") for m in methods],
        per_parent=10,
    )
    hits = [_hit(f"{_MOD}.Router", 0.8, NodeKind.CLASS), _hit(f"{_MOD}.helper", 0.7)]

    out = await step.run(_state(hits, cap=None))

    # 8 slots (LimitStep's default max_results), 2 taken by the original hits.
    assert len(_qnames(out)) == 8


async def test_a_member_scores_its_parent_s_relevance_decayed() -> None:
    out = await _router_and_helper(helper_relevance=0.75)

    assert _relevance_of(out, f"{_MOD}.Router.add") == pytest.approx(0.8 * 0.9)


async def test_a_member_never_scores_above_the_lowest_original_hit() -> None:
    # 0.8 * 0.9 = 0.72 would outrank the 0.5 helper, so any later relevance
    # sort would lift the member over an original hit — capped at 0.5 instead.
    out = await _router_and_helper(helper_relevance=0.5)

    assert _relevance_of(out, f"{_MOD}.Router.add") == pytest.approx(0.5)


async def test_a_module_hit_expands_into_its_classes_and_functions_not_its_imports() -> None:
    imports = _node(f"{_MOD}.<imports>", NodeKind.IMPORT_BLOCK)
    tree = _module(imports, _class(f"{_MOD}.Router"), _node(f"{_MOD}.serve", NodeKind.FUNCTION))
    rows = [_chunk(n.qualified_name, kind=n.kind) for n in tree.children]
    step = await _step([tree], rows)

    out = await step.run(_state([_hit(_MOD, 0.9, NodeKind.MODULE)]))

    assert _qnames(out) == [_MOD, f"{_MOD}.Router", f"{_MOD}.serve"]


async def test_members_of_several_parents_follow_parent_rank_until_the_slots_run_out() -> None:
    tree = _module(_class(f"{_MOD}.A", "a1", "a2"), _class(f"{_MOD}.B", "b1", "b2"))
    rows = [_chunk(f"{_MOD}.{q}") for q in ("A.a1", "A.a2", "B.b1", "B.b2")]
    step = await _step([tree], rows)
    hits = [_hit(f"{_MOD}.B", 0.9, NodeKind.CLASS), _hit(f"{_MOD}.A", 0.8, NodeKind.CLASS)]

    out = await step.run(_state(hits, cap=5))

    assert _qnames(out) == [
        f"{_MOD}.B",
        f"{_MOD}.A",
        f"{_MOD}.B.b1",
        f"{_MOD}.B.b2",
        f"{_MOD}.A.a1",
    ]


async def test_per_parent_caps_the_members_each_parent_contributes() -> None:
    tree = _module(_class(f"{_MOD}.Router", "a", "b", "c"))
    step = await _step([tree], [_chunk(f"{_MOD}.Router.{m}") for m in "abc"], per_parent=2)

    out = await step.run(_state([_hit(f"{_MOD}.Router", 0.9, NodeKind.CLASS)]))

    assert _qnames(out) == [f"{_MOD}.Router", f"{_MOD}.Router.a", f"{_MOD}.Router.b"]


async def test_a_parent_ranked_below_top_s_is_not_expanded() -> None:
    tree = _module(_class(f"{_MOD}.Router", "add"))
    step = await _step([tree], [_chunk(f"{_MOD}.Router.add")], top_s=1)
    hits = [_hit(f"{_MOD}.helper", 0.9), _hit(f"{_MOD}.Router", 0.8, NodeKind.CLASS)]
    state = _state(hits)

    assert await step.run(state) is state


async def test_a_member_already_listed_is_not_appended_again_and_the_next_one_fills_in() -> None:
    tree = _module(_class(f"{_MOD}.Router", "add", "remove", "clear"))
    rows = [_chunk(f"{_MOD}.Router.{m}") for m in ("add", "remove", "clear")]
    step = await _step([tree], rows, per_parent=2)
    hits = [_hit(f"{_MOD}.Router", 0.9, NodeKind.CLASS), _hit(f"{_MOD}.Router.add", 0.8)]

    out = await step.run(_state(hits))

    assert _qnames(out) == [
        f"{_MOD}.Router",
        f"{_MOD}.Router.add",
        f"{_MOD}.Router.remove",
        f"{_MOD}.Router.clear",
    ]


async def test_a_member_with_no_chunk_row_is_skipped() -> None:
    tree = _module(_class(f"{_MOD}.Router", "add", "remove"))
    step = await _step([tree], [_chunk(f"{_MOD}.Router.remove")])

    out = await step.run(_state([_hit(f"{_MOD}.Router", 0.9, NodeKind.CLASS)]))

    assert _qnames(out) == [f"{_MOD}.Router", f"{_MOD}.Router.remove"]


async def test_hits_from_one_module_load_its_tree_once() -> None:
    tree_store = InMemoryDocumentTreeStore()
    await tree_store.save_many(
        [_module(_class(f"{_MOD}.A", "a1"), _class(f"{_MOD}.B", "b1"))], package=_PKG
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert([_chunk(f"{_MOD}.A.a1"), _chunk(f"{_MOD}.B.b1")])
    step = ChildExpandStep(uow_factory=make_fake_uow_factory(trees=tree_store, chunks=chunk_store))
    hits = [_hit(f"{_MOD}.A", 0.9, NodeKind.CLASS), _hit(f"{_MOD}.B", 0.8, NodeKind.CLASS)]

    await step.run(_state(hits))

    assert [call.method for call in tree_store.calls].count("load") == 1


# ── the safety contract: nothing to add ⇒ the very same state ─────────────


async def test_hits_that_already_fill_the_window_come_back_unchanged() -> None:
    tree = _module(_class(f"{_MOD}.Router", "add"))
    step = await _step([tree], [_chunk(f"{_MOD}.Router.add")])
    state = _state([_hit(f"{_MOD}.Router", 0.9, NodeKind.CLASS), _hit(f"{_MOD}.f", 0.8)], cap=2)

    assert await step.run(state) is state


async def test_no_class_or_module_among_the_top_hits_comes_back_unchanged() -> None:
    step = await _step([_module(_class(f"{_MOD}.Router", "add"))], [_chunk(f"{_MOD}.Router.add")])
    state = _state([_hit(f"{_MOD}.helper", 0.9), _hit(f"{_MOD}.Router.add", 0.8)])

    assert await step.run(state) is state


async def test_a_parent_whose_tree_is_missing_comes_back_unchanged() -> None:
    step = await _step([], [_chunk(f"{_MOD}.Router.add")])
    state = _state([_hit(f"{_MOD}.Router", 0.9, NodeKind.CLASS)])

    assert await step.run(state) is state


@pytest.mark.parametrize(
    "candidates",
    [None, ChunkList(items=()), ModuleMemberList(items=())],
    ids=["none", "empty", "members"],
)
async def test_no_chunk_hits_come_back_unchanged(candidates: object) -> None:
    step = await _step()
    state = RetrieverState(query=SearchQuery(terms="q", max_results=8), candidates=candidates)  # type: ignore[arg-type]

    assert await step.run(state) is state


# ── registration + defaults ───────────────────────────────────────────────


def test_a_bare_yaml_entry_builds_the_step_with_the_spec_defaults() -> None:
    step = step_registry.build({"type": "child_expand"}, _ctx())

    assert isinstance(step, ChildExpandStep)
    # The spec's values (#393: top_s 3, per_parent 5, decay 0.9) — known literals.
    assert (step.top_s, step.per_parent, step.decay) == (3, 5, 0.9)


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("top_s", 0),
        ("top_s", True),
        ("per_parent", 0),
        ("per_parent", 2.5),
        ("decay", 0.0),
        ("decay", 1.5),
        ("decay", float("nan")),
    ],
)
def test_an_out_of_range_knob_is_refused_naming_the_key_and_value(key: str, bad: object) -> None:
    with pytest.raises(ValueError, match=rf"ChildExpandStep\.{key}.*{bad!r}"):
        ChildExpandStep.from_dict({"type": "child_expand", key: bad}, _ctx())


def test_the_step_needs_a_uow_factory_to_build() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        ChildExpandStep.from_dict({"type": "child_expand"}, BuildContext(uow_factory=None))


def test_the_steps_package_registers_the_step_for_pipeline_yaml() -> None:
    # Pipeline assembly resolves `type:` names after importing the steps
    # package, so the package export is what makes an overlay YAML loadable.
    from pydocs_mcp.retrieval import steps

    assert steps.ChildExpandStep is step_registry.get("child_expand")


def test_defaults_live_once_omitted_from_yaml_and_restored_from_the_same_source() -> None:
    default = ChildExpandStep(uow_factory=make_fake_uow_factory())

    rebuilt = step_registry.build(default.to_dict(), _ctx())

    assert default.to_dict() == {"type": "child_expand"}
    assert isinstance(rebuilt, ChildExpandStep)
    assert (rebuilt.top_s, rebuilt.per_parent, rebuilt.decay) == (
        default.top_s,
        default.per_parent,
        default.decay,
    )


def test_non_default_knobs_round_trip_through_yaml() -> None:
    step = ChildExpandStep(
        uow_factory=make_fake_uow_factory(), top_s=2, per_parent=4, decay=0.5, name="kids"
    )

    rebuilt = step_registry.build(step.to_dict(), _ctx())

    assert step.to_dict() == {
        "type": "child_expand",
        "top_s": 2,
        "per_parent": 4,
        "decay": 0.5,
        "name": "kids",
    }
    assert isinstance(rebuilt, ChildExpandStep)
    assert (rebuilt.top_s, rebuilt.per_parent, rebuilt.decay, rebuilt.name) == (2, 4, 0.5, "kids")


# ── overlay-only: nothing shipped reaches the step ─────────────────────────


def _types_in(node: object) -> set[str]:
    """Every step ``type`` named anywhere in a pipeline YAML, nested forms included."""
    if isinstance(node, list):
        return set().union(*map(_types_in, node))
    if not isinstance(node, dict):
        return set()
    own = {node["type"]} if isinstance(node.get("type"), str) else set()
    return own.union(*map(_types_in, node.values()))


def test_no_shipped_pipeline_names_the_step() -> None:
    shipped = sorted(_SHIPPED_PIPELINES.glob("*.yaml"))

    named = [p.name for p in shipped if "child_expand" in _types_in(_load_yaml(p))]

    assert len(shipped) >= 18 and named == []


def test_no_default_search_route_reaches_the_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Shipped defaults only: no env path, no ./pydocs-mcp.yaml, no ~/.config file.
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    routes = AppConfig.load().pipelines["chunk"].routes

    reached = set().union(
        *(_types_in(_load_yaml(_resolve_pipeline_path(r.pipeline_path))) for r in routes)
    )

    assert "graph_expand" in reached  # the walk sees the shipped default's steps
    assert "child_expand" not in reached


# ── the displacement guard, as a property ─────────────────────────────────

_SEEDS = range(300)


def _all_nodes(root: DocumentNode) -> list[DocumentNode]:
    nodes = [root]
    for child in root.children:
        nodes.extend(_all_nodes(child))
    return nodes


def _random_world(rng: random.Random) -> tuple[DocumentNode, list[Chunk], list[Chunk]]:
    """A module of classes / functions, a chunk row per node, and a ranked
    sample of those nodes as the original hits (any kind, any count)."""
    classes = [
        _class(f"{_MOD}.C{c}", *(f"m{i}" for i in range(rng.randint(0, 6))))
        for c in range(rng.randint(0, 4))
    ]
    functions = [_node(f"{_MOD}.f{i}", NodeKind.FUNCTION) for i in range(rng.randint(0, 3))]
    tree = _module(*classes, *functions)
    nodes = _all_nodes(tree)
    rows = [_chunk(n.qualified_name, kind=n.kind) for n in nodes]
    picked = rng.sample(nodes, rng.randint(0, len(nodes)))
    scores = sorted((rng.uniform(-0.2, 1.0) for _ in picked), reverse=True)
    hits = [_chunk(n.qualified_name, s, kind=n.kind) for n, s in zip(picked, scores, strict=True)]
    return tree, rows, hits


async def test_no_original_hit_the_limit_keeps_is_ever_displaced() -> None:
    expanded_cases = 0
    for seed in _SEEDS:
        rng = random.Random(seed)
        tree, rows, hits = _random_world(rng)
        step = await _step([tree], rows, top_s=rng.randint(1, 4), per_parent=rng.randint(1, 6))
        state = _state(hits, cap=rng.choice([None, *range(1, 13)]))

        expanded = await step.run(state)
        limited = await LimitStep().run(expanded)
        baseline = await LimitStep().run(state)

        assert isinstance(expanded.candidates, ChunkList)
        assert expanded.candidates.items[: len(hits)] == tuple(hits), f"seed {seed}"
        kept = baseline.candidates.items
        assert limited.candidates.items[: len(kept)] == kept, f"seed {seed}"
        assert rows_dropped_by_limit(limited) == rows_dropped_by_limit(baseline), f"seed {seed}"
        expanded_cases += len(expanded.candidates.items) > len(hits)
    # Not vacuous: the sweep must actually append members in many worlds.
    assert expanded_cases >= 30
