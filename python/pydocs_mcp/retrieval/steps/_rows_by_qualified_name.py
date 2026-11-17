"""Chunk rows addressed by qualified name, as the expansion steps read them.

Graph expansion and child expansion both turn a set of qualified names into
chunk rows. The two rules they share live here, once: which rows a search
may read (the branch pin, spec §6.4, #312) and which row stands for a name
when several carry it (the longest text).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

from pydocs_mcp.filters import FieldIn
from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.filter_helpers import with_branch_pin
from pydocs_mcp.storage.protocols import UnitOfWork

_QNAME_KEY = "qualified_name"

_Key = TypeVar("_Key", bound=Hashable)


async def chunks_named_on_branch(
    uow: UnitOfWork, qnames: tuple[str, ...], branch: str
) -> list[Chunk]:
    """The chunk rows carrying any of ``qnames`` that ``branch`` holds.

    A pinned search (``branch`` non-empty) reads its branch plus the
    dependency tier; ``""`` — the unpinned search — reads every row carrying
    the name. Passes ``FieldIn`` so a real SQLite backend filters via
    ``IN (...)``; callers still narrow client-side, because some stores (the
    test fakes) ignore filter-tree predicates and return everything.
    """
    named = FieldIn(field=_QNAME_KEY, values=qnames)
    pinned = with_branch_pin(named, branch, target_field="chunk") if branch else named
    return await uow.chunks.list(filter=pinned)


def longest_text_row_per_key(
    rows: Iterable[Chunk], key: Callable[[Chunk], _Key | None]
) -> dict[_Key, Chunk]:
    """One representative row per ``key`` — the longest text; ``None`` keys are skipped.

    Example: ``longest_text_row_per_key(rows, key=lambda r: r.metadata.get("qualified_name"))``.
    """
    best: dict[_Key, Chunk] = {}
    for row in rows:
        row_key = key(row)
        if row_key is None:
            continue
        incumbent = best.get(row_key)
        if incumbent is None or len(row.text) > len(incumbent.text):
            best[row_key] = row
    return best


__all__ = ("chunks_named_on_branch", "longest_text_row_per_key")
