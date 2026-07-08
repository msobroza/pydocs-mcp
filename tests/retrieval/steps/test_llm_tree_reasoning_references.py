"""AC-8: include_references=True populates scratch['tree.ranked.refs']."""

from __future__ import annotations

import json

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _tree() -> DocumentNode:
    return DocumentNode(
        node_id="r",
        qualified_name="proj.foo",
        title="foo",
        kind=NodeKind.FUNCTION,
        source_path="f.py",
        start_line=1,
        end_line=5,
        text="foo body",
        content_hash="",
        summary="foo summary",
        extra_metadata={},
        parent_id=None,
        children=(),
    )


def _state(q: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=q, max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )


@pytest.mark.asyncio
async def test_include_references_off_skips_refs_lookup() -> None:
    """Default off — no .refs scratch key written, no reference reads."""
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
        }
    )
    refs = [
        NodeReference(
            from_package="__project__",
            from_node_id="bar-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    ]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="foo body",
                metadata={
                    "qualified_name": "proj.foo",
                    "package": "__project__",
                },
            ),
        )
    )
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs, package="__project__")
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=False,  # default
    )
    out = await step.run(_state("q"))
    assert "tree.ranked" in out.scratch
    assert "tree.ranked.refs" not in out.scratch
    # And no reference lookups happened — the contract is "stay silent".
    assert not any(
        c.method in {"find_by_name", "find_callers", "find_callees"} for c in ref_store.calls
    )


@pytest.mark.asyncio
async def test_include_references_on_writes_refs_scratch() -> None:
    """Opt-in — .refs scratch key carries callers of every picked node."""
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": ["proj.foo"]}),
        }
    )
    refs = [
        NodeReference(
            from_package="__project__",
            from_node_id="bar-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
        NodeReference(
            from_package="__project__",
            from_node_id="baz-node",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    ]
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="foo body",
                metadata={
                    "qualified_name": "proj.foo",
                    "package": "__project__",
                },
            ),
        )
    )
    ref_store = InMemoryReferenceStore()
    await ref_store.save_many(refs, package="__project__")
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [_tree()]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=True,
        reference_neighbors_limit=5,
    )
    out = await step.run(_state("q"))
    assert "tree.ranked.refs" in out.scratch
    surfaced = out.scratch["tree.ranked.refs"]
    assert len(surfaced) == 2
    # Both referenced rows should be the ones we seeded, surfaced by to_name.
    assert {r.from_node_id for r in surfaced} == {"bar-node", "baz-node"}
    assert all(r.to_name == "proj.foo" for r in surfaced)


# ── reference_neighbors_limit: from_dict validation ────────────────────────


def _ctx() -> BuildContext:
    return BuildContext(
        llm_client=FakeLlmClient(responses={}),
        uow_factory=make_fake_uow_factory(),
    )


@pytest.mark.parametrize("bad", [0, -1])
def test_from_dict_rejects_nonpositive_reference_neighbors_limit(bad: int) -> None:
    """A non-positive limit would silently become a tail-dropping slice
    (``callers[:-1]``) or disable the opted-in feature entirely
    (``callers[:0]``) — fail fast at YAML-build time instead, mirroring
    the doc_excerpt_max_chars guard."""
    with pytest.raises(ValueError, match="reference_neighbors_limit"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning", "reference_neighbors_limit": bad},
            _ctx(),
        )


def test_from_dict_rejects_bool_reference_neighbors_limit() -> None:
    """bool is an int subclass in Python; True/False must not silently pass
    the positive-int check and become ``callers[:1]`` / ``callers[:0]``."""
    with pytest.raises(ValueError, match="reference_neighbors_limit"):
        LlmTreeReasoningStep.from_dict(
            {"type": "llm_tree_reasoning", "reference_neighbors_limit": False},
            _ctx(),
        )


# ── reference_neighbors_limit: per-target cap applied before dedupe ────────


@pytest.mark.asyncio
async def test_reference_neighbors_limit_caps_per_target_before_dedupe() -> None:
    """7 callers seeded for one picked qname with limit=5 -> exactly 5
    surface (per-target cap, not a global cap applied after dedupe).

    A second picked qname contributes a caller row with the SAME dedupe key
    (from_package, from_node_id, to_name, kind) as one already surfaced for
    the first qname's capped set — regression coverage for "dedupe happens
    after the per-target cap", not before it.
    """
    callers_for_foo = [
        NodeReference(
            from_package="__project__",
            from_node_id=f"caller-{i}",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        )
        for i in range(7)
    ]
    # Duplicate-key ref across two picks: same identity tuple as
    # callers_for_foo[0], but attached to the second picked qname's lookup
    # (to_name differs, so it is NOT a dedupe collision by itself — it
    # exists purely to prove the cap is per-target: proj.bar's own 1 caller
    # must all surface, uncapped by proj.foo's excess).
    callers_for_bar = [
        NodeReference(
            from_package="__project__",
            from_node_id="caller-bar-0",
            to_name="proj.bar",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
        # Exact duplicate row also saved under proj.foo's lookup identity —
        # proves dedupe collapses it even though it arrives via a
        # different picked-qname loop iteration.
        NodeReference(
            from_package="__project__",
            from_node_id="caller-0",
            to_name="proj.foo",
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ),
    ]
    tree = DocumentNode(
        node_id="r",
        qualified_name="proj.foo",
        title="foo",
        kind=NodeKind.FUNCTION,
        source_path="f.py",
        start_line=1,
        end_line=5,
        text="foo body",
        content_hash="",
        summary="foo summary",
        extra_metadata={},
        parent_id=None,
        children=(
            DocumentNode(
                node_id="r2",
                qualified_name="proj.bar",
                title="bar",
                kind=NodeKind.FUNCTION,
                source_path="f.py",
                start_line=6,
                end_line=10,
                text="bar body",
                content_hash="",
                summary="bar summary",
                extra_metadata={},
                parent_id="r",
                children=(),
            ),
        ),
    )
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": ["proj.foo", "proj.bar"]}),
        }
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(
        (
            Chunk(
                text="foo body", metadata={"qualified_name": "proj.foo", "package": "__project__"}
            ),
            Chunk(
                text="bar body", metadata={"qualified_name": "proj.bar", "package": "__project__"}
            ),
        )
    )
    ref_store = InMemoryReferenceStore()
    # find_by_name("proj.foo", ...) must see all 7 + the duplicate-key row.
    await ref_store.save_many(
        [*callers_for_foo, callers_for_bar[1]],
        package="__project__",
    )
    # find_by_name("proj.bar", ...) sees its own single caller.
    await ref_store.save_many([callers_for_bar[0]], package="__project__")
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(by_package={"__project__": [tree]}),
        chunks=chunk_store,
        references=ref_store,
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=True,
        reference_neighbors_limit=5,
    )
    out = await step.run(_state("q"))
    surfaced = out.scratch["tree.ranked.refs"]

    foo_surfaced = [r for r in surfaced if r.to_name == "proj.foo"]
    bar_surfaced = [r for r in surfaced if r.to_name == "proj.bar"]
    # Per-target cap: proj.foo had 8 candidate rows (7 + 1 exact dup of
    # caller-0) but only the first 5 in store-return order pass the cap —
    # the cap must be applied BEFORE dedupe, so at most 5 survive, never
    # fewer just because a duplicate happened to occupy a capped slot.
    assert len(foo_surfaced) == 5
    # proj.bar's own single caller is unaffected by proj.foo's excess —
    # proves the cap is per-target, not a shared global budget.
    assert len(bar_surfaced) == 1
    assert bar_surfaced[0].from_node_id == "caller-bar-0"
