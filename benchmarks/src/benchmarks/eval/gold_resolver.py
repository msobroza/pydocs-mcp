"""Unified ground-truth resolution layer (spec §5, locked decision §5).

A per-system, **opt-in** ``GoldResolver`` runs in the runner between
``system.search()`` and the metric loop. It outputs a ``frozenset[str]``
of *identity keys* that are ground-truth for one task; the runner injects
that set into a fresh ``EvalTask`` (frozen gold -> ``dataclasses.replace``)
under ``gold.extra["resolved_chunk_ids"]``. Every metric then asks the
single ``is_relevant(item, task)`` predicate
(``benchmarks/eval/metrics/_relevance.py``) instead of branching on
fuzzy-vs-exact per metric — the resolver hides the strategy difference
behind one Protocol.

**Identity scheme (``_item_key``):** ``chunk:{chunk_id}`` when an item
carries a store chunk id, else ``rank:{rank}``. The ``chunk:`` / ``rank:``
namespacing means an int chunk-id can never collide with an int rank.

**Two resolvers ship here:**

- ``PydocsFuzzyGoldResolver`` (**eager**) — for ``PydocsMcpSystem`` in
  native mode. Enumerates pydocs's own chunk store via an INJECTED
  ``uow_factory`` and fuzzy-matches each stored chunk's text against the
  gold ``doc_contents``, returning ``chunk:{store_id}`` for every hit. The
  injected factory (not a db path) is what keeps this module's tests
  hermetic: a fake factory -> fake UoW -> canned ``chunks.list`` exercises
  the scan with no ``pydocs_mcp`` import. The only ``pydocs_mcp`` touch is
  a DEFERRED ``from pydocs_mcp.deps import normalize_package_name`` inside
  ``resolve``.
- ``LazyFuzzyGoldResolver`` — for non-enumerable stores
  (Context7/Neuledge, and ``PydocsMcpSystem`` in composite mode whose
  chunks carry ``id=None``). Fuzzy-matches the *retrieved* items' text
  only.

(Oracle indexing's exact-match ``PydocsOracleGoldResolver`` plugs in via
this same Protocol in a later task.)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rapidfuzz import fuzz

if TYPE_CHECKING:
    from .datasets.base_dataset import EvalTask
    from .systems.base_system import RetrievedItem

# Single source of truth for the fuzzy match threshold (rapidfuzz
# ``partial_ratio`` is on a 0-100 scale). 85 is the locked-decision value.
_DEFAULT_FUZZ_THRESHOLD = 85


def _item_key(item: RetrievedItem) -> str:
    """Identity key for a retrieved item, shared by the resolvers and
    ``is_relevant``. ``chunk:{id}`` when the item carries a store chunk id,
    else ``rank:{rank}`` — the ``chunk:`` / ``rank:`` prefixes keep an int
    chunk-id from ever colliding with an int rank."""
    if item.chunk_id is not None:
        return f"chunk:{item.chunk_id}"
    return f"rank:{item.rank}"


@runtime_checkable
class GoldResolver(Protocol):
    """Maps one (task, retrieved) pair to the ``frozenset`` of identity
    keys that are ground-truth for that task. ``runtime_checkable`` so the
    runner can gate on the system carrying one (via ``HasGoldResolver``)."""

    async def resolve(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class PydocsFuzzyGoldResolver:
    """Eager resolver: scan pydocs's chunk store, fuzzy-match each stored
    chunk against gold ``doc_contents``.

    ``uow_factory`` is INJECTED (a ``Callable[[], UnitOfWork]`` that opens
    an async-context-manager UoW exposing ``.chunks.list``) rather than a
    db path, so hermetic tests pass a fake factory with NO ``pydocs_mcp``
    import. ``PydocsMcpSystem.gold_resolver`` builds the real one from its
    post-index ``_db_path`` via ``build_sqlite_uow_factory``.
    """

    uow_factory: Callable[[], object]
    threshold: int = _DEFAULT_FUZZ_THRESHOLD

    async def resolve(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> frozenset[str]:
        # WHY: cheap no-op BEFORE any DB access. ``PydocsMcpSystem`` is
        # ``HasGoldResolver`` even for RepoQA tasks (no ``doc_contents``),
        # so the runner calls ``resolve()`` there too — the early return
        # makes that zero-cost (never opens a UoW, never reads metadata).
        doc_contents = task.gold.extra.get("doc_contents", ())
        if not doc_contents:
            return frozenset()

        # WHY (coherence): pydocs stores package names lower+underscored
        # (``deps.normalize_package_name`` does ``.lower().replace("-","_")``,
        # e.g. ``scikit-learn`` -> ``scikit_learn``). DS-1000's
        # ``metadata["library"]`` is PyPI-canonical (still hyphenated), so we
        # MUST normalize before filtering or the store-scan reads zero rows.
        # Deferred import keeps this module importable without pydocs_mcp.
        from pydocs_mcp.deps import normalize_package_name

        library = task.metadata.get("library", "")
        pkg = normalize_package_name(library) if library else None
        filter_ = {"package": pkg} if pkg else None

        # UoW read path (CLAUDE.md §"Creating new application services"): no
        # ``commit()`` needed — the ``__aexit__`` rollback is a no-op on a
        # read-only transaction.
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(filter=filter_)

        matched: set[str] = set()
        for chunk in chunks:
            # Composite/budgeted chunks carry id=None — they can't be
            # id-matched to a store row, so skip them even on a content hit.
            if chunk.id is None:
                continue
            score = max(
                fuzz.partial_ratio(gold, chunk.text) for gold in doc_contents
            )
            if score >= self.threshold:
                matched.add(f"chunk:{chunk.id}")
        return frozenset(matched)


@dataclass(frozen=True, slots=True)
class LazyFuzzyGoldResolver:
    """Lazy resolver for non-enumerable stores (Context7/Neuledge, and
    pydocs composite mode). Fuzzy-matches the *retrieved* items' text
    against gold ``doc_contents`` and returns their ``_item_key``s."""

    threshold: int = _DEFAULT_FUZZ_THRESHOLD

    async def resolve(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> frozenset[str]:
        doc_contents = task.gold.extra.get("doc_contents", ())
        if not doc_contents:
            return frozenset()
        matched: set[str] = set()
        for item in retrieved:
            score = max(
                fuzz.partial_ratio(gold, item.text) for gold in doc_contents
            )
            if score >= self.threshold:
                matched.add(_item_key(item))
        return frozenset(matched)


__all__ = [
    "GoldResolver",
    "LazyFuzzyGoldResolver",
    "PydocsFuzzyGoldResolver",
]
