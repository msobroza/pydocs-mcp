"""ParentRollupStep — collapse sibling results into their parent.

A rerank-only step: when enough children of one ``DocumentNode`` parent
are co-retrieved (>= ``_MIN_SIBLINGS`` sibling hits AND kind-resolved
coverage of the parent's chunk-emitting children), the siblings are
replaced by the parent's own indexed chunk at the group's best rank.
Replaces candidates only — adds nothing on failure paths and falls
through to the unchanged input on every data-shaped failure condition
(missing tree, missing parent chunk row, gates unmet, malformed
metadata). Reads ``document_trees`` via ``uow.trees`` and ``chunks`` via
``uow.chunks`` in one read-only UoW per call. Spec:
docs/superpowers/specs/2026-07-14-parent-rollup-retriever-step-design.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import ClassVar

from pydocs_mcp.extraction.model.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: per-kind coverage thresholds — see the spec's §3.6 table. Class
# rollup is eager (top-K caps the numerator hard for classes); a whole-
# module rollup swallows the most granularity, so it demands the
# strongest evidence; doc headings sit in between.
_DEFAULT_MIN_COVERAGE = 0.5
_DEFAULT_MIN_COVERAGE_BY_KIND: Mapping[str, float] = MappingProxyType(
    {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}
)
# WHY: structural floor, not a tunable — collapsing a single retrieved
# child is pure information loss (same list length, less specific
# result), so no deployment wants 1. Not a dataclass field, never
# serialized, absent from _YAML_KEYS.
_MIN_SIBLINGS = 2
_DEFAULT_NAME = "parent_rollup"
_QNAME_KEY = "qualified_name"
_PACKAGE_KEY = "package"
_MODULE_KEY = "module"
_VALID_KIND_KEYS = frozenset(k.value for k in NodeKind)


def _validated_coverage_mapping(raw: object) -> dict[str, float]:
    """Validate a YAML-parsed ``min_coverage_by_kind`` value pre-construction."""
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"ParentRollupStep.min_coverage_by_kind must be a mapping of "
            f"NodeKind value -> float; got {raw!r}."
        )
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _VALID_KIND_KEYS:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind key {key!r} is not a "
                f"NodeKind value; valid keys: {sorted(_VALID_KIND_KEYS)}."
            )
        # bool is an int subclass, but `class: true` is a YAML typo, not a
        # threshold. 0.0 is allowed: explicit per-kind opt-in to maximum
        # eagerness (the sibling floor still gates).
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind[{key!r}] must be a "
                f"float in [0.0, 1.0]; got {value!r}."
            )
        out[key] = float(value)
    return out


@dataclass(slots=True)
class _Rollup:
    """One applied parent rollup — module-private mutable accumulator.

    ``claimed`` holds the ORIGINAL candidate indices this rollup consumed
    (hit children + same-qname self-folds). Mutable so an AST
    duplicate-parent merge can union claims into the first emission.
    """

    chunk: Chunk
    claimed: set[int]


def _candidate_key(chunk: Chunk) -> tuple[str, str, str] | None:
    """(package, module, qualified_name) — None when any is missing/blank."""
    package = chunk.metadata.get(_PACKAGE_KEY)
    module = chunk.metadata.get(_MODULE_KEY)
    qname = chunk.metadata.get(_QNAME_KEY)
    if not package or not module or not qname:
        return None
    return (str(package), str(module), str(qname))


def _group_candidates(
    items: tuple[Chunk, ...],
) -> dict[tuple[str, str], dict[str, list[int]]]:
    """Group candidate indices by (package, module), then by qualified_name."""
    groups: dict[tuple[str, str], dict[str, list[int]]] = {}
    for i, chunk in enumerate(items):
        key = _candidate_key(chunk)
        if key is None:
            continue
        package, module, qname = key
        groups.setdefault((package, module), {}).setdefault(qname, []).append(i)
    return groups


def _post_order(root: DocumentNode) -> list[DocumentNode]:
    """Post-order DFS: children before parent, document order among siblings.

    Iterative (deep subpackage chains must not hit the recursion limit).
    Guarantees deeper-before-shallower along any ancestor chain so the
    more specific collapse claims its indices first (spec §3.2 Phase 4).
    """
    out: list[DocumentNode] = []
    stack: list[tuple[DocumentNode, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            out.append(node)
            continue
        stack.append((node, True))
        for child in reversed(node.children):
            stack.append((child, False))
    return out


def _emitting_children(node: DocumentNode) -> list[DocumentNode]:
    """Children that emit a chunk — kept in lockstep with
    ``tree_flatten._should_emit`` (re-implemented over children — see there
    for the source of truth)."""
    return [c for c in node.children if c.kind not in STRUCTURAL_ONLY_KINDS and c.text.strip()]


def _fold_relevance(rollup: _Rollup, items: tuple[Chunk, ...]) -> Chunk:
    """relevance = max over the group's non-None values; None if all None."""
    values = [items[i].relevance for i in rollup.claimed if items[i].relevance is not None]
    if not values:
        return rollup.chunk
    return replace(rollup.chunk, relevance=max(values))


def _rebuild(items: tuple[Chunk, ...], rollups: list[_Rollup]) -> list[Chunk]:
    """Emit each parent at its group's lowest index; drop other claimed indices."""
    emit_at = {min(r.claimed): r for r in rollups}
    claimed_all: set[int] = set().union(*(r.claimed for r in rollups))
    out: list[Chunk] = []
    for i, chunk in enumerate(items):
        rollup = emit_at.get(i)
        if rollup is not None:
            out.append(_fold_relevance(rollup, items))
        elif i not in claimed_all:
            out.append(chunk)
    return _dedup_against_parents(out, rollups)


def _dedup_against_parents(out: list[Chunk], rollups: list[_Rollup]) -> list[Chunk]:
    """Cross-group dedup (spec §3.2 Phase 5): a surviving candidate whose
    (qualified_name, content_hash) equals an emitted parent's is dropped,
    keeping the single lowest-index occurrence."""
    parent_keys = {(r.chunk.metadata.get(_QNAME_KEY), r.chunk.content_hash) for r in rollups}
    seen: set[tuple] = set()
    deduped: list[Chunk] = []
    for chunk in out:
        key = (chunk.metadata.get(_QNAME_KEY), chunk.content_hash)
        if key in parent_keys:
            if key in seen:
                continue
            seen.add(key)
        deduped.append(chunk)
    return deduped


@step_registry.register("parent_rollup")
@dataclass(frozen=True, slots=True)
class ParentRollupStep(RetrieverStep):
    """Collapse co-retrieved sibling chunks into their parent's chunk."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    min_coverage: float = field(default=_DEFAULT_MIN_COVERAGE, kw_only=True)
    min_coverage_by_kind: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MIN_COVERAGE_BY_KIND),
        kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("min_coverage", "min_coverage_by_kind", "name")

    def __post_init__(self) -> None:
        # Read-only normalization — the Chunk.metadata precedent
        # (models.py __post_init__): frozen+slots forbids assignment,
        # not object.__setattr__; dataclasses.replace re-runs this and
        # harmlessly re-wraps.
        object.__setattr__(
            self,
            "min_coverage_by_kind",
            MappingProxyType(dict(self.min_coverage_by_kind)),
        )

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        groups = _group_candidates(candidates.items)
        if not groups:
            return state
        rollups: list[_Rollup] = []
        claimed: set[int] = set()
        async with self.uow_factory() as uow:
            for (package, module), by_qname in groups.items():
                tree = await uow.trees.load(package, module)
                if tree is None:
                    continue
                rollups.extend(
                    await self._apply_group(
                        uow, tree, package, module, by_qname, candidates.items, claimed
                    )
                )
        if not rollups:
            return state
        rebuilt = _rebuild(candidates.items, rollups)
        return replace(state, candidates=ChunkList(items=tuple(rebuilt)))

    def _threshold(self, kind: NodeKind) -> float:
        # Kind-resolved from the loaded tree node — never chunk metadata.
        return self.min_coverage_by_kind.get(kind.value, self.min_coverage)

    def _hit_qnames(
        self,
        parent: DocumentNode,
        by_qname: dict[str, list[int]],
        claimed: set[int],
    ) -> set[str]:
        """Child qnames with >=1 unclaimed candidate index (set semantics —
        AST duplicate qnames count once)."""
        hits: set[str] = set()
        for child in _emitting_children(parent):
            indices = by_qname.get(child.qualified_name, ())
            if any(i not in claimed for i in indices):
                hits.add(child.qualified_name)
        return hits

    def _gates_pass(self, parent: DocumentNode, hits: set[str]) -> bool:
        if not parent.text.strip() or len(hits) < _MIN_SIBLINGS:
            return False
        emitting = _emitting_children(parent)
        if not emitting:
            return False
        # >= is normative: equality triggers (spec §3.2 Phase 3, AC24).
        return len(hits) / len(emitting) >= self._threshold(parent.kind)

    def _claim_for(
        self,
        parent: DocumentNode,
        hits: set[str],
        by_qname: dict[str, list[int]],
        claimed: set[int],
    ) -> set[int]:
        """Atomic claim: every unclaimed index of a hit child, PLUS the
        self-fold — same-group candidates bearing the parent's own qname."""
        claim: set[int] = set()
        for qname in hits:
            claim.update(i for i in by_qname.get(qname, ()) if i not in claimed)
        claim.update(i for i in by_qname.get(parent.qualified_name, ()) if i not in claimed)
        return claim

    @staticmethod
    def _reuse_in_list(
        parent: DocumentNode,
        claim: set[int],
        items: tuple[Chunk, ...],
    ) -> Chunk | None:
        """The self-folded in-list parent chunk, if the parent was a candidate."""
        for i in sorted(claim):
            if items[i].metadata.get(_QNAME_KEY) == parent.qualified_name:
                return items[i]
        return None

    async def _resolve_parent_chunk(
        self,
        uow: UnitOfWork,
        parent: DocumentNode,
        claim: set[int],
        package: str,
        module: str,
        items: tuple[Chunk, ...],
    ) -> Chunk | None:
        """The parent's chunk: the self-folded in-list candidate if present,
        else the fetched row. None when no row exists (abandonment §3.5)."""
        reused = self._reuse_in_list(parent, claim, items)
        if reused is not None:
            return reused
        rows = await uow.chunks.list(
            filter={
                _PACKAGE_KEY: package,
                _MODULE_KEY: module,
                _QNAME_KEY: parent.qualified_name,
            },
            limit=1,
        )
        return rows[0] if rows else None

    async def _apply_group(
        self,
        uow: UnitOfWork,
        tree: DocumentNode,
        package: str,
        module: str,
        by_qname: dict[str, list[int]],
        items: tuple[Chunk, ...],
        claimed: set[int],
    ) -> list[_Rollup]:
        """Post-order application with atomic claims (spec §3.2 Phase 4).

        Gates evaluate against UNCLAIMED indices only, so a deeper rollup's
        claim is invisible to its ancestors (no cascade) and an abandoned
        fetch releases its claim implicitly (the claim is registered only
        after a successful fetch/reuse).
        """
        rollups: list[_Rollup] = []
        by_parent_qname: dict[str, _Rollup] = {}
        for parent in _post_order(tree):
            hits = self._hit_qnames(parent, by_qname, claimed)
            if not self._gates_pass(parent, hits):
                continue
            claim = self._claim_for(parent, hits, by_qname, claimed)
            existing = by_parent_qname.get(parent.qualified_name)
            if existing is not None:
                # AST duplicate parent qname: merge into the earlier rollup —
                # one emission at the lowest combined index, no second fetch.
                existing.claimed |= claim
                claimed |= claim
                continue
            parent_chunk = await self._resolve_parent_chunk(
                uow, parent, claim, package, module, items
            )
            if parent_chunk is None:
                continue  # abandonment — claim never registered (§3.5)
            rollup = _Rollup(chunk=parent_chunk, claimed=claim)
            by_parent_qname[parent.qualified_name] = rollup
            rollups.append(rollup)
            claimed |= claim
        return rollups

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="parent_rollup", keys=self._YAML_KEYS)

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> ParentRollupStep:
        if context.uow_factory is None:
            raise ValueError(
                "ParentRollupStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this.",
            )
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        if not 0.0 < kwargs["min_coverage"] <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage must be in (0.0, 1.0]; "
                f"got {kwargs['min_coverage']!r}.",
            )
        kwargs["min_coverage_by_kind"] = _validated_coverage_mapping(kwargs["min_coverage_by_kind"])
        return cls(uow_factory=context.uow_factory, **kwargs)


__all__ = ("ParentRollupStep",)
