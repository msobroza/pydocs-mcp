"""I5: LlmTreeReasoningStep fans out find_by_name lookups via asyncio.gather.

Pins the call-shape regression: the reference-enrichment branch
(``include_references=True``) must dispatch the N ``find_by_name`` calls
concurrently, not in a serial for-loop. Validated via a synchronization
gate inside the fake reference store — if the step were serial only one
call would ever be in flight at a time, the gate would deadlock, and the
test would time out.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.llm_tree_reasoning import LlmTreeReasoningStep
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    FakeLlmClient,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)


@dataclass
class _ConcurrentReferenceStore:
    """Fake ReferenceStore that proves find_by_name calls overlap.

    The first N-1 callers block on ``barrier`` until N concurrent
    in-flight calls accumulate; the Nth caller flips the barrier and
    every call returns. A serial implementation would only ever have
    one call in flight, deadlocking until the test's timeout.
    """

    expected_concurrency: int
    in_flight: int = 0
    peak_concurrency: int = 0
    barrier: asyncio.Event = field(default_factory=asyncio.Event)
    call_args: list[str] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def save_many(self, refs, *, package: str, uow=None) -> None:  # unused but Protocol-shape
        return None

    async def find_callers(self, *, target_node_id: str):  # unused
        return []

    async def find_callees(self, *, from_node_id: str):  # unused
        return []

    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]:
        async with self._lock:
            self.call_args.append(to_name)
            self.in_flight += 1
            self.peak_concurrency = max(self.peak_concurrency, self.in_flight)
            if self.in_flight >= self.expected_concurrency:
                self.barrier.set()
        # All concurrent callers wait here; with gather they all proceed;
        # with a serial for-loop only one ever reaches this point so the
        # barrier never flips and the test times out.
        await self.barrier.wait()
        async with self._lock:
            self.in_flight -= 1
        return []

    async def delete_for_package(self, package: str, *, uow=None) -> None:
        return None

    async def delete_all(self, *, uow=None) -> None:
        return None


def _node(qname: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname,
        kind=NodeKind.FUNCTION,
        source_path="f.py",
        start_line=1,
        end_line=5,
        text=f"{qname} body",
        content_hash="",
        summary=f"{qname} summary",
        extra_metadata={},
        parent_id=None,
        children=(),
    )


def _chunk(qname: str) -> Chunk:
    return Chunk(
        text=f"{qname} body",
        metadata={
            "qualified_name": qname,
            "package": "__project__",
        },
    )


def _state(q: str) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms=q, max_results=10),
        candidates=None,
        result=None,
        scratch={},
    )


@pytest.mark.asyncio
async def test_find_by_name_uses_gather_not_serial() -> None:
    """Pin asyncio.gather fan-out: N picks → N concurrent find_by_name calls.

    The concurrent reference store blocks on a barrier until N callers
    are in flight. A serial for-loop would deadlock (only one call ever
    in flight), so we wrap with a tight timeout — if the step refactor
    regresses back to serial, the test fails loudly via TimeoutError.
    """
    picks = ("proj.a", "proj.b", "proj.c")
    llm = FakeLlmClient(
        responses={
            "q": json.dumps({"thinking": "", "node_list": list(picks)}),
        }
    )
    chunk_store = InMemoryChunkStore()
    await chunk_store.upsert(tuple(_chunk(qn) for qn in picks))
    ref_store = _ConcurrentReferenceStore(expected_concurrency=len(picks))
    uow_factory = make_fake_uow_factory(
        trees=InMemoryDocumentTreeStore(
            by_package={
                "__project__": [_node(qn) for qn in picks],
            }
        ),
        chunks=chunk_store,
        references=ref_store,  # type: ignore[arg-type]
    )
    step = LlmTreeReasoningStep(
        llm_client=llm,
        uow_factory=uow_factory,
        include_references=True,
    )

    # Tight timeout — under serial dispatch the store deadlocks on the
    # barrier and we hit TimeoutError; under gather it returns promptly.
    await asyncio.wait_for(step.run(_state("q")), timeout=2.0)

    assert len(ref_store.call_args) == len(picks)
    assert set(ref_store.call_args) == set(picks)
    # Peak concurrency proves all N calls overlapped at the same instant.
    assert ref_store.peak_concurrency == len(picks)
