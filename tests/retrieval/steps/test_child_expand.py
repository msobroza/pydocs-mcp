"""ChildExpandStep — fill a search's free result slots with the children of its
leading class / module candidates, never displacing an original (#393).

The step seam (``ChildExpandStep.run`` over the named fakes) and the YAML
codec / registry seam. The displacement guard as a property, the shipped
defaults, real SQLite and the wired tool live in the sibling files.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.extraction.model.document_node import NodeKind
from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.child_expand import ChildExpandStep
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)
from tests.retrieval.steps._child_expand_builders import (
    MOD,
    PKG,
    candidate,
    chunk_row,
    class_node,
    module_node,
    qnames_in,
    ranked_state,
    step_over,
    tree_node,
)


def _ctx() -> BuildContext:
    return BuildContext(uow_factory=make_fake_uow_factory())


def _relevance_of(state: RetrieverState, qname: str) -> float | None:
    assert isinstance(state.candidates, ChunkList)
    (chunk,) = [c for c in state.candidates.items if c.metadata["qualified_name"] == qname]
    return chunk.relevance


async def _router_then_helper(helper_relevance: float) -> RetrieverState:
    """Class ``Router`` (0.8) then function ``helper``; ``Router.add`` free to append."""
    step = await step_over(
        [module_node(class_node(f"{MOD}.Router", "add"))], [chunk_row(f"{MOD}.Router.add")]
    )
    ranked = [candidate(f"{MOD}.Router", 0.8), candidate(f"{MOD}.helper", helper_relevance)]
    return await step.run(ranked_state(ranked, cap=4))


# ── expansion ─────────────────────────────────────────────────────────────


async def test_a_class_candidate_s_children_fill_the_free_slots_below_the_originals() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "add", "remove"))
    rows = [chunk_row(f"{MOD}.Router.add"), chunk_row(f"{MOD}.Router.remove")]
    step = await step_over([tree], rows)
    ranked = [candidate(f"{MOD}.Router", 0.8), candidate(f"{MOD}.helper", 0.7)]

    out = await step.run(ranked_state(ranked, cap=5))

    assert qnames_in(out) == [
        f"{MOD}.Router",
        f"{MOD}.helper",
        f"{MOD}.Router.add",
        f"{MOD}.Router.remove",
    ]


async def test_a_query_without_a_cap_fills_up_to_the_limit_step_s_default_of_8() -> None:
    methods = [f"m{i}" for i in range(10)]
    step = await step_over(
        [module_node(class_node(f"{MOD}.Router", *methods))],
        [chunk_row(f"{MOD}.Router.{m}") for m in methods],
        per_parent=10,
    )
    ranked = [candidate(f"{MOD}.Router", 0.8), candidate(f"{MOD}.helper", 0.7)]

    out = await step.run(ranked_state(ranked, cap=None))

    # 8 slots (LimitStep's default max_results), 2 taken by the originals.
    assert len(qnames_in(out)) == 8


async def test_a_child_scores_its_parent_s_relevance_decayed() -> None:
    out = await _router_then_helper(helper_relevance=0.75)

    assert _relevance_of(out, f"{MOD}.Router.add") == pytest.approx(0.8 * 0.9)


async def test_a_child_never_scores_above_the_lowest_original() -> None:
    # 0.8 * 0.9 = 0.72 would outrank the 0.5 helper, so any later relevance
    # sort would lift the child over an original — capped at 0.5 instead.
    out = await _router_then_helper(helper_relevance=0.5)

    assert _relevance_of(out, f"{MOD}.Router.add") == pytest.approx(0.5)


async def test_a_module_candidate_expands_into_its_classes_and_functions_not_its_imports() -> None:
    imports = tree_node(f"{MOD}.<imports>", NodeKind.IMPORT_BLOCK)
    serve = tree_node(f"{MOD}.serve", NodeKind.FUNCTION)
    tree = module_node(imports, class_node(f"{MOD}.Router"), serve)
    step = await step_over([tree], [chunk_row(n.qualified_name) for n in tree.children])

    out = await step.run(ranked_state([candidate(MOD, 0.9)]))

    assert qnames_in(out) == [MOD, f"{MOD}.Router", f"{MOD}.serve"]


async def test_children_of_several_parents_follow_parent_rank_until_the_slots_run_out() -> None:
    tree = module_node(class_node(f"{MOD}.A", "a1", "a2"), class_node(f"{MOD}.B", "b1", "b2"))
    rows = [chunk_row(f"{MOD}.{q}") for q in ("A.a1", "A.a2", "B.b1", "B.b2")]
    step = await step_over([tree], rows)
    ranked = [candidate(f"{MOD}.B", 0.9), candidate(f"{MOD}.A", 0.8)]

    out = await step.run(ranked_state(ranked, cap=5))

    assert qnames_in(out) == [
        f"{MOD}.B",
        f"{MOD}.A",
        f"{MOD}.B.b1",
        f"{MOD}.B.b2",
        f"{MOD}.A.a1",
    ]


async def test_per_parent_caps_the_children_each_parent_contributes() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "a", "b", "c"))
    step = await step_over([tree], [chunk_row(f"{MOD}.Router.{m}") for m in "abc"], per_parent=2)

    out = await step.run(ranked_state([candidate(f"{MOD}.Router", 0.9)]))

    assert qnames_in(out) == [f"{MOD}.Router", f"{MOD}.Router.a", f"{MOD}.Router.b"]


async def test_a_parent_ranked_below_top_s_is_not_expanded() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "add"))
    step = await step_over([tree], [chunk_row(f"{MOD}.Router.add")], top_s=1)
    state = ranked_state([candidate(f"{MOD}.helper", 0.9), candidate(f"{MOD}.Router", 0.8)])

    assert await step.run(state) is state


async def test_a_child_already_listed_is_not_appended_again_and_the_next_one_fills_in() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "add", "remove", "clear"))
    rows = [chunk_row(f"{MOD}.Router.{m}") for m in ("add", "remove", "clear")]
    step = await step_over([tree], rows, per_parent=2)
    ranked = [candidate(f"{MOD}.Router", 0.9), candidate(f"{MOD}.Router.add", 0.8)]

    out = await step.run(ranked_state(ranked))

    assert qnames_in(out) == [
        f"{MOD}.Router",
        f"{MOD}.Router.add",
        f"{MOD}.Router.remove",
        f"{MOD}.Router.clear",
    ]


async def test_a_child_with_several_rows_is_appended_once_as_its_longest_row() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "add"))
    short = chunk_row(f"{MOD}.Router.add", text="def add(): ...")
    longest = chunk_row(f"{MOD}.Router.add", text="def add(self, item): return item")
    step = await step_over([tree], [short, longest])

    out = await step.run(ranked_state([candidate(f"{MOD}.Router", 0.9)]))

    assert isinstance(out.candidates, ChunkList)
    assert [c.text for c in out.candidates.items[1:]] == [longest.text]


async def test_a_child_with_no_chunk_row_is_skipped() -> None:
    tree = module_node(class_node(f"{MOD}.Router", "add", "remove"))
    step = await step_over([tree], [chunk_row(f"{MOD}.Router.remove")])

    out = await step.run(ranked_state([candidate(f"{MOD}.Router", 0.9)]))

    assert qnames_in(out) == [f"{MOD}.Router", f"{MOD}.Router.remove"]


async def test_candidates_from_one_module_load_its_tree_once() -> None:
    tree_store = InMemoryDocumentTreeStore()
    await tree_store.save_many(
        [module_node(class_node(f"{MOD}.A", "a1"), class_node(f"{MOD}.B", "b1"))], package=PKG
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert([chunk_row(f"{MOD}.A.a1"), chunk_row(f"{MOD}.B.b1")])
    step = ChildExpandStep(uow_factory=make_fake_uow_factory(trees=tree_store, chunks=chunk_store))

    await step.run(ranked_state([candidate(f"{MOD}.A", 0.9), candidate(f"{MOD}.B", 0.8)]))

    assert [call.method for call in tree_store.calls].count("load") == 1


# ── the safety contract: nothing to add ⇒ the very same state ─────────────


async def test_originals_that_already_fill_the_window_come_back_unchanged() -> None:
    step = await step_over(
        [module_node(class_node(f"{MOD}.Router", "add"))], [chunk_row(f"{MOD}.Router.add")]
    )
    state = ranked_state([candidate(f"{MOD}.Router", 0.9), candidate(f"{MOD}.f", 0.8)], cap=2)

    assert await step.run(state) is state


async def test_no_class_or_module_among_the_leading_candidates_comes_back_unchanged() -> None:
    step = await step_over(
        [module_node(class_node(f"{MOD}.Router", "add"))], [chunk_row(f"{MOD}.Router.add")]
    )
    state = ranked_state([candidate(f"{MOD}.helper", 0.9), candidate(f"{MOD}.Router.add", 0.8)])

    assert await step.run(state) is state


async def test_a_function_candidate_is_never_a_parent_even_with_a_nested_definition() -> None:
    inner = tree_node(f"{MOD}.outer.inner", NodeKind.FUNCTION)
    outer = tree_node(f"{MOD}.outer", NodeKind.FUNCTION, inner)
    step = await step_over([module_node(outer)], [chunk_row(inner.qualified_name)])
    state = ranked_state([candidate(f"{MOD}.outer", 0.9)])

    assert await step.run(state) is state


async def test_a_parent_whose_tree_is_missing_comes_back_unchanged() -> None:
    step = await step_over([], [chunk_row(f"{MOD}.Router.add")])
    state = ranked_state([candidate(f"{MOD}.Router", 0.9)])

    assert await step.run(state) is state


@pytest.mark.parametrize(
    "metadata",
    [{"package": PKG, "module": MOD}, {"package": PKG, "qualified_name": f"{MOD}.Router"}],
    ids=["no-qualified-name", "no-module"],
)
async def test_a_candidate_row_with_no_tree_address_comes_back_unchanged(
    metadata: dict[str, str],
) -> None:
    step = await step_over(
        [module_node(class_node(f"{MOD}.Router", "add"))], [chunk_row(f"{MOD}.Router.add")]
    )
    state = ranked_state([Chunk(text="prose", relevance=0.9, metadata=metadata)])

    assert await step.run(state) is state


@pytest.mark.parametrize(
    "candidates",
    [None, ChunkList(items=()), ModuleMemberList(items=())],
    ids=["none", "empty", "module-member-list"],
)
async def test_no_chunk_candidates_come_back_unchanged(
    candidates: ChunkList | ModuleMemberList | None,
) -> None:
    step = await step_over()
    state = RetrieverState(query=SearchQuery(terms="q", max_results=8), candidates=candidates)

    assert await step.run(state) is state


# ── registration + defaults (the codec seam) ──────────────────────────────


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
