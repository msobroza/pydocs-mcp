"""ChildExpandStep — fill a search's free result slots with its class / module candidates' children.

Experiment step for #393, available through a YAML overlay only: no shipped
pipeline references it. When a leading candidate is a class or a module, the
agent cannot see what it defines without a follow-up call; this step appends
the children themselves (classes, functions, methods) as extra rows,
modelled on :class:`~pydocs_mcp.retrieval.steps.graph_expand.GraphExpandStep`.

Flow (runs AFTER the final ranking — ``top_k_filter`` — and BEFORE ``limit``):

1. The window: the cap the limit step will apply (:func:`limit_step_cap` —
   the query's own cap, else the limit step's default ``max_results``). The
   free slots are the window minus the original candidates; none ⇒ unchanged.
2. Parents: among the first ``top_s`` candidates, those the document tree
   says are a class or a module (one tree load per distinct (package, module)).
3. Children: each parent's first ``per_parent`` children not already listed,
   in document order, read in one branch-pinned chunk query.
4. The displacement guard: children are appended BELOW the last original
   candidate and only into the free slots, so every original the limit keeps
   stays at its rank and the limit drops no extra row. A child scores its
   parent's relevance times ``decay``, capped at the lowest original
   relevance, so a later stable relevance sort cannot lift it above an
   original either.

Consequence worth knowing before measuring it: a free slot exists only when a
search returns fewer candidates than its limit. A dense fetch returns its
whole candidate budget whenever the filtered corpus holds that many chunks,
so on a broad dense search the step adds nothing — by construction.

Children are not re-filtered, as graph expansion's neighbours are not: they
share their parent's package and module, so package / scope / module filters
hold, but a title or origin filter does not. Across a multi-project union the
guard holds per project, not across projects (the union re-sorts every
project's rows by relevance). Safety contract: anything missing (no free
slot, no tree, no row) returns the input state unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from itertools import islice
from typing import ClassVar, NamedTuple

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.retrieval.steps._rows_by_qualified_name import (
    chunks_named_on_branch,
    longest_text_row_per_key,
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
# Children worth a slot: what a class or module defines. An import block or a
# docstring example is context the parent candidate already shows.
_CHILD_KINDS = frozenset({NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD})

_QNAME_KEY = "qualified_name"


class _RowKey(NamedTuple):
    """What a listed candidate and an appended child are matched on."""

    package: str
    qualified_name: str


def _row_key(chunk: Chunk) -> _RowKey | None:
    """The row's (package, qualified_name), or None when either is missing."""
    package = chunk.metadata.get(ChunkFilterField.PACKAGE.value)
    qname = chunk.metadata.get(_QNAME_KEY)
    if not package or not qname:
        return None
    return _RowKey(str(package), str(qname))


@dataclass(frozen=True, slots=True)
class _LeadingCandidate:
    """One of the first ``top_s`` candidates, with the address of its tree node."""

    candidate: Chunk
    key: _RowKey
    module: str


def _as_leading_candidate(candidate: Chunk) -> _LeadingCandidate | None:
    """The candidate with its tree address, or None when its row lacks one."""
    key = _row_key(candidate)
    module = candidate.metadata.get(ChunkFilterField.MODULE.value)
    if key is None or not module:
        return None
    return _LeadingCandidate(candidate=candidate, key=key, module=str(module))


@dataclass(frozen=True, slots=True)
class _PlannedChildren:
    """A class / module candidate and the children planned to follow it."""

    parent: Chunk
    package: str
    child_qnames: tuple[str, ...]


def _parent_node(tree: DocumentNode | None, qname: str) -> DocumentNode | None:
    """The candidate's node when the tree says it is a class or a module.

    WHY the tree and never the row: the chunks table persists no ``kind``
    (``row_to_chunk``), so a fetched candidate cannot say what it is — the
    parent-rollup step's rule.
    """
    node = tree.find_node_by_qualified_name(qname) if tree is not None else None
    return node if node is not None and node.kind in _PARENT_KINDS else None


@step_registry.register("child_expand")
@dataclass(frozen=True, slots=True)
class ChildExpandStep(RetrieverStep):
    """Append the children of the leading class / module candidates into free result slots.

    ``top_s`` — how many leading candidates may act as parents; ``per_parent``
    — how many children each parent may contribute; ``decay`` — a child's
    score as a fraction of its parent's. Strict-gate ``from_dict`` like graph
    expansion: a missing ``BuildContext.uow_factory`` is a wiring bug, and an
    out-of-range knob a tuning typo, so both fail at pipeline load.
    """

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
        free_slots = limit_step_cap(state.query) - len(candidates.items)
        leading = [c for c in map(_as_leading_candidate, candidates.items[: self.top_s]) if c]
        if free_slots <= 0 or not leading:
            return state
        listed = {key for key in map(_row_key, candidates.items) if key is not None}
        branch = state.query.branch
        async with self.uow_factory() as uow:
            plans = await self._plan_children(uow, branch, leading, listed)
            qnames = tuple(qname for plan in plans for qname in plan.child_qnames)
            rows = await chunks_named_on_branch(uow, qnames, branch) if qnames else []
        floor = min(c.relevance or 0.0 for c in candidates.items)
        children = _scored_children(plans, rows, listed, decay=self.decay, floor=floor)
        if not children:
            return state
        appended = (*candidates.items, *children[:free_slots])
        return replace(state, candidates=ChunkList(items=appended))

    async def _plan_children(
        self,
        uow: UnitOfWork,
        branch: str,
        leading: list[_LeadingCandidate],
        listed: set[_RowKey],
    ) -> list[_PlannedChildren]:
        """Each class / module candidate's first ``per_parent`` unlisted
        children — one tree load per distinct (package, module), on the
        searched branch (spec §6.4, #312)."""
        trees: dict[tuple[str, str], DocumentNode | None] = {}
        plans: list[_PlannedChildren] = []
        for lead in leading:
            where = (lead.key.package, lead.module)
            if where not in trees:
                trees[where] = await uow.trees.load(*where, branch=branch or None)
            node = _parent_node(trees[where], lead.key.qualified_name)
            if node is not None:
                qnames = self._unlisted_child_qnames(node, lead.key.package, listed)
                plans.append(_PlannedChildren(lead.candidate, lead.key.package, qnames))
        return plans

    def _unlisted_child_qnames(
        self, node: DocumentNode, package: str, listed: set[_RowKey]
    ) -> tuple[str, ...]:
        """The first ``per_parent`` children worth a slot, in document order."""
        fresh = (
            child.qualified_name
            for child in node.children
            if child.kind in _CHILD_KINDS and _RowKey(package, child.qualified_name) not in listed
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
    outside (0, 1] would score a child above its own parent — a tuning typo
    either way, so the overlay fails at load rather than measuring an A/A.
    """
    for key in ("top_s", "per_parent"):
        value = kwargs[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"ChildExpandStep.{key} must be an integer >= 1; got {value!r}.")
    decay = kwargs["decay"]
    if isinstance(decay, bool) or not isinstance(decay, int | float) or not 0.0 < decay <= 1.0:
        raise ValueError(f"ChildExpandStep.decay must be a number in (0, 1]; got {decay!r}.")


def _scored_children(
    plans: list[_PlannedChildren],
    rows: list[Chunk],
    listed: set[_RowKey],
    *,
    decay: float,
    floor: float,
) -> list[Chunk]:
    """The planned children that have a row, parents in rank order, each once.

    A child scores its parent's relevance times ``decay`` (graph expansion's
    one-hop score), capped at ``floor`` — the lowest original relevance — so
    a later relevance sort can never lift a child over an original candidate.
    """
    by_key = longest_text_row_per_key(rows, key=_row_key)
    seen = set(listed)
    out: list[Chunk] = []
    for plan, key in _planned_child_keys(plans):
        row = by_key.get(key)
        if row is None or key in seen:
            continue
        seen.add(key)
        score = min((plan.parent.relevance or 0.0) * decay, floor)
        out.append(replace(row, relevance=score))
    return out


def _planned_child_keys(
    plans: list[_PlannedChildren],
) -> Iterator[tuple[_PlannedChildren, _RowKey]]:
    for plan in plans:
        yield from ((plan, _RowKey(plan.package, qname)) for qname in plan.child_qnames)


__all__ = ("ChildExpandStep",)
