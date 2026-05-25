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

Oracle indexing's exact-match ``PydocsOracleGoldResolver`` also ships here
(for ``PydocsOracleSystem``): same injected-``uow_factory`` shape, but it
matches the gold ``doc_ids`` against each stored chunk's ``title`` metadata
exactly (no fuzz), returning ``chunk:{store_id}`` for every hit.
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
class PydocsOracleGoldResolver:
    """Exact-match resolver for ``PydocsOracleSystem`` (oracle-indexing mode).

    Where the fuzzy resolver scores stored chunk *text* against gold
    ``doc_contents``, this resolver matches on the gold ``doc_ids`` directly:
    the oracle indexer wrote each ``code-rag-bench/library-documentation`` row's
    ``doc_id`` into the chunk's ``title`` metadata (the ``chunks.title`` column),
    so an EXACT string membership of that title against ``gold.extra["doc_ids"]``
    is the ground truth. No ``rapidfuzz`` — exactness is the point of an oracle.

    ``uow_factory`` is INJECTED (a ``Callable[[], UnitOfWork]`` opening an
    async-context-manager UoW exposing ``.chunks.list``) so the matching test
    pattern as ``PydocsFuzzyGoldResolver`` holds; ``PydocsOracleSystem`` builds
    the real one from its post-index ``_db_path`` via ``build_sqlite_uow_factory``.

    Identity coherence with Task 3's ``_item_key``: oracle chunks are real store
    rows, so the resolver returns ``chunk:{store_id}`` (NOT the doc_id string) for
    every matched chunk — lining up with the ``chunk:{chunk_id}`` keys
    ``search()`` stamps onto the ranked items.
    """

    uow_factory: Callable[[], object]

    async def resolve(
        self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]
    ) -> frozenset[str]:
        # WHY: cheap no-op BEFORE any DB access — same RepoQA-safety rationale
        # as the fuzzy resolver. A task with no gold doc_ids never opens a UoW.
        gold_ids = task.gold.extra.get("doc_ids", ())
        if not gold_ids:
            return frozenset()

        # Deferred imports keep this module importable without pydocs_mcp.
        from pydocs_mcp.deps import normalize_package_name
        from pydocs_mcp.models import ChunkFilterField

        # WHY (coherence): the oracle indexer normalized each row's library the
        # SAME way before writing the Package + chunk ``package`` metadata, so
        # we MUST normalize the task's library here or the store scan reads zero
        # rows. ``doc_id`` global uniqueness keeps exact-match correct, and the
        # package filter keeps the scan fast.
        library = task.metadata.get("library", "")
        pkg = normalize_package_name(library)
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(filter={"package": pkg})

        # EXACT string membership: the chunk's ``title`` metadata IS the row's
        # doc_id. id=None chunks can't be id-matched to a store row -> skip.
        return frozenset(
            f"chunk:{c.id}"
            for c in chunks
            if c.id is not None
            and c.metadata.get(ChunkFilterField.TITLE.value) in gold_ids
        )


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
    "PydocsOracleGoldResolver",
]
