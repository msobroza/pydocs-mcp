"""ChildExpandStep — fill a search's free result slots with its top hits' members.

Experiment step for #393, available through a YAML overlay only: no shipped
pipeline references it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from itertools import islice
from typing import ClassVar

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.filters import FieldIn
from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.filter_helpers import with_branch_pin
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.retrieval.steps.limit import limit_step_cap
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: single source of truth for every default — referenced from the field
# defaults, to_dict omit-when-default and from_dict's YAML fallback (#393).
_DEFAULT_TOP_S = 3
_DEFAULT_PER_PARENT = 5
_DEFAULT_DECAY = 0.9
_DEFAULT_NAME = "child_expand"

_PARENT_KINDS = frozenset({NodeKind.CLASS, NodeKind.MODULE})
_MEMBER_KINDS = frozenset({NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD})

_QNAME_KEY = "qualified_name"
_PACKAGE_KEY = "package"
_MODULE_KEY = "module"

# (package, qualified_name) — what a listed row and an appended child share.
_RowKey = tuple[str, str]


def _row_key(chunk: Chunk) -> _RowKey | None:
    package = chunk.metadata.get(_PACKAGE_KEY)
    qname = chunk.metadata.get(_QNAME_KEY)
    if not package or not qname:
        return None
    return (str(package), str(qname))


@dataclass(frozen=True, slots=True)
class _Parent:
    """A top hit, addressed in its module's document tree."""

    hit: Chunk
    package: str
    module: str
    qname: str


def _addressed(hit: Chunk) -> _Parent | None:
    """The hit's tree address, or None when its row lacks one."""
    key = _row_key(hit)
    module = hit.metadata.get(_MODULE_KEY)
    if key is None or not module:
        return None
    return _Parent(hit=hit, package=key[0], module=str(module), qname=key[1])


def _parent_node(tree: DocumentNode | None, qname: str) -> DocumentNode | None:
    """The hit's node when the tree says it is a class or a module.

    WHY the tree and never the row: the chunks table persists no ``kind``
    (``row_to_chunk``), so a fetched hit cannot say what it is — the
    parent-rollup step's rule.
    """
    node = tree.find_node_by_qualified_name(qname) if tree is not None else None
    return node if node is not None and node.kind in _PARENT_KINDS else None


@step_registry.register("child_expand")
@dataclass(frozen=True, slots=True)
class ChildExpandStep(RetrieverStep):
    """Append the members of the top class / module hits into free result slots."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    top_s: int = field(default=_DEFAULT_TOP_S, kw_only=True)
    per_parent: int = field(default=_DEFAULT_PER_PARENT, kw_only=True)
    decay: float = field(default=_DEFAULT_DECAY, kw_only=True)
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("top_s", "per_parent", "decay", "name")

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        free = limit_step_cap(state.query) - len(candidates.items)
        parents = [p for p in map(_addressed, candidates.items[: self.top_s]) if p is not None]
        if free <= 0 or not parents:
            return state
        listed = {key for key in map(_row_key, candidates.items) if key is not None}
        async with self.uow_factory() as uow:
            reads = _BranchReads(uow, state.query.branch)
            plan = await self._plan(reads, parents, listed)
            rows = await reads.chunks_named(tuple(q for _, qnames in plan for q in qnames))
        floor = min(hit.relevance or 0.0 for hit in candidates.items)
        children = _appended(plan, rows, listed, decay=self.decay, floor=floor)[:free]
        if not children:
            return state
        return replace(state, candidates=ChunkList(items=(*candidates.items, *children)))

    async def _plan(
        self, reads: _BranchReads, parents: list[_Parent], listed: set[_RowKey]
    ) -> list[tuple[_Parent, tuple[str, ...]]]:
        """Each parent's first ``per_parent`` unlisted members, in document
        order — one tree load per distinct (package, module)."""
        trees: dict[tuple[str, str], DocumentNode | None] = {}
        plan: list[tuple[_Parent, tuple[str, ...]]] = []
        for parent in parents:
            where = (parent.package, parent.module)
            if where not in trees:
                trees[where] = await reads.tree(*where)
            node = _parent_node(trees[where], parent.qname)
            if node is not None:
                plan.append((parent, self._member_qnames(node, parent.package, listed)))
        return plan

    def _member_qnames(
        self, node: DocumentNode, package: str, listed: set[_RowKey]
    ) -> tuple[str, ...]:
        fresh = (
            child.qualified_name
            for child in node.children
            if child.kind in _MEMBER_KINDS and (package, child.qualified_name) not in listed
        )
        return tuple(islice(fresh, self.per_parent))

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="child_expand", keys=self._YAML_KEYS)

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> ChildExpandStep:
        if context.uow_factory is None:
            raise ValueError(
                "ChildExpandStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this; "
                "tests must pass it explicitly.",
            )
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        _check_knobs(kwargs)
        return cls(uow_factory=context.uow_factory, **kwargs)


def _check_knobs(kwargs: dict) -> None:
    """Refuse a knob that would silently disable the arm or invert its ranking.

    A ``top_s`` / ``per_parent`` below 1 appends nothing, and a ``decay``
    outside (0, 1] would score a member above its own parent — a tuning typo
    either way, so the overlay fails at load rather than measuring an A/A.
    """
    for key in ("top_s", "per_parent"):
        value = kwargs[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"ChildExpandStep.{key} must be an integer >= 1; got {value!r}.")
    decay = kwargs["decay"]
    if isinstance(decay, bool) or not isinstance(decay, int | float) or not 0.0 < decay <= 1.0:
        raise ValueError(f"ChildExpandStep.decay must be a number in (0, 1]; got {decay!r}.")


def _appended(
    plan: list[tuple[_Parent, tuple[str, ...]]],
    rows: list[Chunk],
    listed: set[_RowKey],
    *,
    decay: float,
    floor: float,
) -> list[Chunk]:
    """The planned children that have a row, parents in rank order, each once.

    A child scores its parent's relevance times ``decay`` (graph expansion's
    one-hop score), capped at ``floor`` — the lowest original relevance — so
    a later relevance sort can never lift a child over an original hit.
    """
    by_key = _representatives(rows)
    seen = set(listed)
    out: list[Chunk] = []
    for parent, key in _planned_keys(plan):
        row = by_key.get(key)
        if row is None or key in seen:
            continue
        seen.add(key)
        score = min((parent.hit.relevance or 0.0) * decay, floor)
        out.append(replace(row, relevance=score))
    return out


def _planned_keys(
    plan: list[tuple[_Parent, tuple[str, ...]]],
) -> Iterator[tuple[_Parent, _RowKey]]:
    for parent, qnames in plan:
        yield from ((parent, (parent.package, qname)) for qname in qnames)


def _representatives(rows: list[Chunk]) -> dict[_RowKey, Chunk]:
    """One row per (package, qualified_name) — the longest text, graph expansion's rule."""
    best: dict[_RowKey, Chunk] = {}
    for row in rows:
        key = _row_key(row)
        if key is None:
            continue
        incumbent = best.get(key)
        if incumbent is None or len(row.text) > len(incumbent.text):
            best[key] = row
    return best


@dataclass(frozen=True, slots=True)
class _BranchReads:
    """The trees and chunk rows one search reads (spec §6.4, #312).

    Graph expansion's rule: a pinned search (``branch`` non-empty) reads its
    branch plus the dependency tier; ``""`` reads the served branch.
    """

    uow: UnitOfWork
    branch: str

    async def tree(self, package: str, module: str) -> DocumentNode | None:
        return await self.uow.trees.load(package, module, branch=self.branch or None)

    async def chunks_named(self, qnames: tuple[str, ...]) -> list[Chunk]:
        if not qnames:
            return []
        named = FieldIn(field=_QNAME_KEY, values=qnames)
        pinned = with_branch_pin(named, self.branch, target_field="chunk") if self.branch else named
        return await self.uow.chunks.list(filter=pinned)


__all__ = ("ChildExpandStep",)
