"""Search on a named branch: which branch a search pins, and per-branch hydration
(spec §6.4, #312).

The router resolves the request's selector once (#311); :func:`search_branch_pin`
turns that resolution into the branch the search pushes down —
``SearchQuery.branch``, which the pre-filter step ANDs into every fetcher's
tree — and :class:`RequestBranchPins` answers it for every bundle a multi-repo
search covers, asked by the search router for exactly the bundles it searches.
:class:`MembershipChunkHydrator` then gives the rows a pinned search returns the
selected branch's spans: a chunk row is shared by every branch holding it, and
its own span columns are whichever pass wrote them first.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydocs_mcp.models import PROJECT_PACKAGE_NAME, BranchSlice, Chunk, ChunkFilterField

if TYPE_CHECKING:
    # Types only: ``DocsSearch`` and the multi-repo router import this module,
    # and neither the resolution's import graph (git plumbing, retirement
    # messages) nor the router's is any of their business.
    from pydocs_mcp.application.branch_resolution import ResolvedBranch
    from pydocs_mcp.application.multi_project_search import ProjectServices
    from pydocs_mcp.storage.branch_records import ChunkMembership
    from pydocs_mcp.storage.protocols import UnitOfWork

_SPAN_KEYS = (
    ChunkFilterField.SOURCE_PATH.value,
    ChunkFilterField.START_LINE.value,
    ChunkFilterField.END_LINE.value,
)


def search_branch_pin(branch: ResolvedBranch) -> str:
    """The branch a search pins, or ``""`` to leave the query as it was.

    A bundle holding one branch pins nothing: every project row is that
    branch's, so the pin could not change the answer — and without it the
    query, its SQL and the dense ANN path stay byte for byte what they were
    (spec R7). The null resolution and the non-git placeholder are that case.
    """
    # TODO(P2 diff slice): valid only while the sole branch holds no DIFF-slice
    # membership — P1 writes TREE rows only. Spec §6.4 keeps diff hunks out of
    # every request by stamping ``slice``; once P2 writes a DIFF slice for a
    # one-branch bundle's working tree, an unpinned query would return those
    # hunks, so the pin (branch + TREE slice) must come back on for it.
    return "" if branch.holds_every_project_row else branch.name


class BranchPinSource(Protocol):
    """The branch one searched bundle pins (``""`` = unpinned).

    The search router asks for exactly the bundles it searches, so which
    bundles a request covers is decided in one place (#312).
    """

    async def pin_of(self, svc: ProjectServices) -> str: ...


@dataclass(frozen=True, slots=True)
class UnpinnedBranchSource:
    """The Null Object: every bundle searches unpinned — the legacy text
    surface, which resolves no branch."""

    async def pin_of(self, svc: ProjectServices) -> str:
        return ""


UNPINNED_BRANCH_SOURCE = UnpinnedBranchSource()


@dataclass(frozen=True, slots=True)
class RequestBranchPins:
    """One request's pins (plan Task 14 Step 6, #312).

    The answering bundle — the one ``meta.project`` names — pins the request's
    own resolution, the one ``meta.branch`` reports. Every other bundle a union
    searches pins its OWN default resolution (selector ``""``), whatever the
    request named: the name was resolved against the answering bundle only, so
    a bundle that never indexed it cannot fail the union. Resolved lazily, for
    the bundles the router searches — ``kind="decision"`` has no union and
    resolves no other bundle.
    """

    answering_db_path: Path
    answering_pin: str
    resolve_default: Callable[[ProjectServices], Awaitable[ResolvedBranch]]

    @classmethod
    def for_request(
        cls,
        answering: ProjectServices,
        resolved: ResolvedBranch,
        resolve_default: Callable[[ProjectServices], Awaitable[ResolvedBranch]],
    ) -> RequestBranchPins:
        return cls(answering.project.db_path, search_branch_pin(resolved), resolve_default)

    async def pin_of(self, svc: ProjectServices) -> str:
        if svc.project.db_path == self.answering_db_path:
            return self.answering_pin
        return search_branch_pin(await self.resolve_default(svc))


class ChunkBranchHydrator(Protocol):
    """Rows a pinned search returns, as the selected branch holds them."""

    async def hydrate_on_branch(
        self, chunks: tuple[Chunk, ...], branch: str
    ) -> tuple[Chunk, ...]: ...


@dataclass(frozen=True, slots=True)
class MembershipChunkHydrator:
    """Hydrates hits from ``branch``'s tree-slice membership (spec §6.1, §6.4).

    A project row takes the branch's span; a project row the branch does not
    hold is dropped — the last guard behind the pushdown, for a step that
    fetched rows outside the pre-filter. Dependency rows and rows without an
    id (a formatter's composite) pass untouched: every branch reads them.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def hydrate_on_branch(self, chunks: tuple[Chunk, ...], branch: str) -> tuple[Chunk, ...]:
        project_ids = [c.id for c in chunks if c.id is not None and _is_project_row(c)]
        if not project_ids:
            return chunks
        async with self.uow_factory() as uow:
            rows = await uow.branch_chunks.membership_of_chunks(
                branch, project_ids, slice=BranchSlice.TREE
            )
        held = {row.chunk_id: row for row in rows}
        return tuple(_with_branch_span(c, held) for c in chunks if _answers_for_branch(c, held))


@dataclass(frozen=True, slots=True)
class NullChunkBranchHydrator:
    """The Null Object: a search service composed without a bundle to read."""

    async def hydrate_on_branch(self, chunks: tuple[Chunk, ...], branch: str) -> tuple[Chunk, ...]:
        return chunks


def _is_project_row(chunk: Chunk) -> bool:
    return chunk.metadata.get(ChunkFilterField.PACKAGE.value) == PROJECT_PACKAGE_NAME


def _answers_for_branch(chunk: Chunk, held: Mapping[int, ChunkMembership]) -> bool:
    return chunk.id is None or not _is_project_row(chunk) or chunk.id in held


def _with_branch_span(chunk: Chunk, held: Mapping[int, ChunkMembership]) -> Chunk:
    """``chunk`` with the span its branch holds it at, keyed like ``row_to_chunk``:
    a path only when non-empty, a line only when known."""
    row = held.get(chunk.id) if chunk.id is not None else None
    if row is None:
        return chunk
    metadata = {k: v for k, v in chunk.metadata.items() if k not in _SPAN_KEYS}
    spans = zip(_SPAN_KEYS, (row.source_path or None, row.start_line, row.end_line), strict=True)
    metadata.update((key, value) for key, value in spans if value is not None)
    return replace(chunk, metadata=metadata)


__all__ = (
    "UNPINNED_BRANCH_SOURCE",
    "BranchPinSource",
    "ChunkBranchHydrator",
    "MembershipChunkHydrator",
    "NullChunkBranchHydrator",
    "RequestBranchPins",
    "UnpinnedBranchSource",
    "search_branch_pin",
)
