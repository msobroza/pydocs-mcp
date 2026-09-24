"""Search and lookup on a named branch: which branch a request pins, and
per-branch hydration (spec §6.4, #312, #313).

The router resolves the request's selector once (#311); :func:`search_branch_pin`
turns that resolution into the branch the search pushes down —
``SearchQuery.branch``, which the pre-filter step ANDs into every fetcher's
tree — and :class:`RequestBranchPins` answers it for every bundle a multi-repo
search covers, asked by the search router for exactly the bundles it searches.
:class:`MembershipChunkHydrator` then gives the rows a pinned search returns the
selected branch's spans: a chunk row is shared by every branch holding it, and
its own span columns are whichever pass wrote them first.

The symbol tools (#313) take the same pins: :func:`read_branch_of` is the
branch their index reads name, :func:`services_reading_branch` binds one
bundle's lookup and source services to it, and :func:`chunk_filter_on_branch`
pins the chunk reads those services make.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchSlice,
    Chunk,
    ChunkFilterField,
    ModuleMemberFilterField,
)
from pydocs_mcp.retrieval.filter_helpers import chunk_branch_pin_fields

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
_Row = TypeVar("_Row")


def chunk_filter_on_branch(
    filter: Mapping[str, object], branch: str | None
) -> Mapping[str, object]:
    """``filter`` pinned to ``branch``'s tree slice (spec §6.4, #313).

    A chunk row is shared by every branch holding it, so a lookup that reads
    chunks names the branch the way a search pins it: its membership, tree
    slice (dependency rows pass). ``None`` returns ``filter`` unchanged —
    unpinned (#312), exact only on the one-branch bundles ``read_branch_of``
    gives ``None`` for — so a read naming no branch keeps its SQL.
    """
    if branch is None:
        return filter
    return {**filter, **chunk_branch_pin_fields(branch)}


def member_filter_on_branch(
    filter: Mapping[str, object], branch: str | None
) -> Mapping[str, object]:
    """``filter`` reading ``branch``'s members plus the dependency tier (#313);
    ``None`` leaves the key out, which the member store reads as the served
    default branch — never every branch."""
    return filter if branch is None else {**filter, ModuleMemberFilterField.BRANCH.value: branch}


def read_branch_of(branch: ResolvedBranch) -> str | None:
    """The branch a lookup's index reads take (#313): the #312 search pin, or
    ``None`` — the served default — where the pin is empty (one branch, or no
    branch dimension), so those reads keep today's SQL (spec R7)."""
    return search_branch_pin(branch) or None


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


def services_reading_branch(svc: ProjectServices, branch: str | None) -> ProjectServices:
    """``svc`` whose symbol lookups and ``depth="source"`` reads answer from
    ``branch`` (#313); ``None`` — the served default — returns ``svc`` itself.

    The multi-repo lookup router runs its unchanged routing over services
    bound this way, so a union lookup reads each bundle on the branch its own
    pin names — the #312 union rule — and the workspace fallback consults each
    bundle's branch-bound target resolver.
    """
    if branch is None:
        return svc
    return replace(
        svc,
        lookup=svc.lookup.on_branch(branch),
        symbol_source=svc.symbol_source.on_branch(branch),
    )


async def pinned_services(svc: ProjectServices, pins: BranchPinSource) -> ProjectServices:
    """``svc`` bound to the branch ``pins`` names for it (``""`` → the served default)."""
    return services_reading_branch(svc, await pins.pin_of(svc) or None)


def owners_on_their_pins(
    owned: Sequence[tuple[ProjectServices, _Row]],
    services: Sequence[ProjectServices],
    pins: Sequence[str],
) -> list[tuple[ProjectServices, _Row]]:
    """``owned`` with each owner bound to the pin its bundle searched
    (``services[i]`` pinned ``pins[i]``), so a union's member rows resolve their
    spans on that branch's trees (#313)."""
    bound = {
        id(s): services_reading_branch(s, pin or None)
        for s, pin in zip(services, pins, strict=True)
    }
    return [(bound[id(owner)], row) for owner, row in owned]


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
        if not _project_ids(chunks):
            return chunks
        async with self.uow_factory() as uow:
            hydrated = await chunks_as_branch_holds_them(uow, chunks, branch)
        return hydrated


async def chunks_as_branch_holds_them(
    uow: UnitOfWork, chunks: tuple[Chunk, ...], branch: str
) -> tuple[Chunk, ...]:
    """``chunks`` with ``branch``'s tree-slice spans, inside an open ``uow``.

    The one hydration both a pinned search (:class:`MembershipChunkHydrator`)
    and a symbol-source read on a branch (#313) take: a project row gets the
    branch's span and one the branch does not hold is dropped.
    """
    project_ids = _project_ids(chunks)
    if not project_ids:
        return chunks
    rows = await uow.branch_chunks.membership_of_chunks(branch, project_ids, slice=BranchSlice.TREE)
    held = {row.chunk_id: row for row in rows}
    return tuple(_with_branch_span(c, held) for c in chunks if _answers_for_branch(c, held))


def _project_ids(chunks: tuple[Chunk, ...]) -> list[int]:
    return [c.id for c in chunks if c.id is not None and _is_project_row(c)]


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
    "chunk_filter_on_branch",
    "chunks_as_branch_holds_them",
    "member_filter_on_branch",
    "owners_on_their_pins",
    "pinned_services",
    "read_branch_of",
    "search_branch_pin",
    "services_reading_branch",
)
