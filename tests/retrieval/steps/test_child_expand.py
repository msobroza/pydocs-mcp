"""ChildExpandStep — fill a search's free result slots with the members of its
top class / module hits, never displacing an original hit (#393, overlay-only).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.child_expand import ChildExpandStep
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_PKG = "pkg"
_MOD = "pkg.mod"

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


# ── registration + defaults ───────────────────────────────────────────────


def test_a_bare_yaml_entry_builds_the_step_with_the_spec_defaults() -> None:
    step = step_registry.build({"type": "child_expand"}, _ctx())

    assert isinstance(step, ChildExpandStep)
    # The spec's values (#393: top_s 3, per_parent 5, decay 0.9) — known literals.
    assert (step.top_s, step.per_parent, step.decay) == (3, 5, 0.9)


def test_the_steps_package_registers_the_step_for_pipeline_yaml() -> None:
    # Pipeline assembly resolves `type:` names after importing the steps
    # package, so the package export is what makes an overlay YAML loadable.
    from pydocs_mcp.retrieval import steps

    assert steps.ChildExpandStep is step_registry.get("child_expand")
