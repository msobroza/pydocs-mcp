"""Builders shared by the child_expand tests (#393).

Document trees, chunk rows exactly as the chunks table returns them, and a
step over the named in-memory fakes. A row never carries ``kind``: the table
does not persist one, so only the document tree can say what a candidate is.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.child_expand import ChildExpandStep
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

PKG = "pkg"
MOD = "pkg.mod"


def tree_node(qname: str, kind: NodeKind, *children: DocumentNode) -> DocumentNode:
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


def class_node(qname: str, *method_names: str) -> DocumentNode:
    methods = [tree_node(f"{qname}.{m}", NodeKind.METHOD) for m in method_names]
    return tree_node(qname, NodeKind.CLASS, *methods)


def module_node(*children: DocumentNode, qname: str = MOD) -> DocumentNode:
    return tree_node(qname, NodeKind.MODULE, *children)


def chunk_row(
    qname: str,
    relevance: float | None = None,
    *,
    module: str = MOD,
    package: str = PKG,
    text: str | None = None,
) -> Chunk:
    """A chunk row as the chunks table returns it — no ``kind`` key."""
    return Chunk(
        text=text or f"text of {qname}",
        relevance=relevance,
        metadata={"package": package, "module": module, "qualified_name": qname},
    )


def candidate(qname: str, relevance: float) -> Chunk:
    return chunk_row(qname, relevance)


async def step_over(
    trees: Sequence[DocumentNode] = (),
    rows: Sequence[Chunk] = (),
    **knobs: object,
) -> ChildExpandStep:
    """A step over fakes holding ``trees`` (package ``pkg``) and chunk ``rows``."""
    tree_store = InMemoryDocumentTreeStore()
    chunk_store = InMemoryChunkStore()
    if trees:
        await tree_store.save_many(list(trees), package=PKG)
    if rows:
        await chunk_store.upsert(list(rows))
    factory = make_fake_uow_factory(trees=tree_store, chunks=chunk_store)
    return ChildExpandStep(uow_factory=factory, **knobs)  # type: ignore[arg-type]


def ranked_state(items: Sequence[Chunk], *, cap: int | None = 8) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q", max_results=cap),
        candidates=ChunkList(items=tuple(items)),
    )


def qnames_in(state: RetrieverState) -> list[str]:
    assert isinstance(state.candidates, ChunkList)
    return [c.metadata["qualified_name"] for c in state.candidates.items]
