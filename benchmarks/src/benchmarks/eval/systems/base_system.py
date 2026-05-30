"""System axis contract (spec §4.5, §4.10).

Owns ``RetrievedItem`` and the ``System`` ``@runtime_checkable`` Protocol.
Concrete systems in ``benchmarks/eval/systems/`` implement the Protocol
and are reachable through ``system_registry`` in ``serialization.py`` —
the runner never imports the concretes directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    # WHY: AppConfig is only typed here, not imported, to keep this module
    # importable without pulling the whole pydocs_mcp.retrieval package.
    from pydocs_mcp.retrieval.config import AppConfig

    # WHY: typed-only to avoid a runtime import cycle — ``gold_resolver``
    # imports ``RetrievedItem`` from this module, so importing
    # ``GoldResolver`` at runtime here would be circular. The
    # ``runtime_checkable`` Protocol below only needs the name at type time.
    from ..gold_resolver import GoldResolver


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One item returned by the system under test."""

    rank: int
    text: str
    source_path: str
    qualified_name: str | None = None
    relevance: float | None = None
    # WHY: the ground-truth resolution layer's identity linchpin. For
    # pydocs's enumerable store this is the DB row id (set by ``search()``
    # from ``chunk.id``) so an eager resolver's ``chunk:{store_id}`` keys
    # line up with the ranked items here. ``None`` for composite/blob
    # systems (Context7/Neuledge, pydocs composite mode), where
    # ``_item_key`` falls back to the rank. Additive + last so existing
    # positional ``RetrievedItem(...)`` call sites stay valid.
    chunk_id: int | None = None


@runtime_checkable
class System(Protocol):
    name: str

    async def index(self, corpus_dir: Path, config: AppConfig) -> None: ...

    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]: ...

    async def teardown(self) -> None: ...


@runtime_checkable
class HasLibraryName(Protocol):
    """A system that wants the human-readable library identifier
    (e.g. ``"psf/black"``) seeded from ``task.metadata['repo']`` before
    ``index()``. ``Context7System`` is the primary implementor.

    Opt-in is documented at the type level via ``runtime_checkable`` —
    ``isinstance(sys, HasLibraryName)`` is the runner's gating check
    (equivalent to ``hasattr`` under the hood, but with a name attached).
    """

    library_name: str


@runtime_checkable
class HasLibrary(Protocol):
    """A system that wants the install identifier
    (``"{repo}@{commit[:7]}"``) seeded from ``task.metadata`` before
    ``index()``. ``NeuledgeSystem`` is the primary implementor.

    See ``HasLibraryName`` for the opt-in mechanism.
    """

    library: str


@runtime_checkable
class HasResolvedLibrary(Protocol):
    """A system that exposes the library id it resolved during ``index()``
    so the runner can capture it for scoring. ``Context7System`` is the
    primary implementor (the id is its router's pick, or the oracle).

    See ``HasLibraryName`` for the opt-in mechanism — the runner gates on
    ``isinstance(system, HasResolvedLibrary)`` and records
    ``last_resolved_library_id`` into ``gold.extra`` between ``search()``
    and scoring. Systems that don't expose it are a strict no-op.
    """

    last_resolved_library_id: str | None


@runtime_checkable
class IndexesDependencies(Protocol):
    """A system whose indexer can be told whether to index the corpus's
    declared dependencies. The runner sets ``index_dependencies`` once per
    sweep before ``index()``:

    - ``True`` — index the corpus's declared deps. Reference-project datasets
      (DS-1000, supplied via ``--corpus-dir``) whose declared libraries ARE
      the search target rely on this.
    - ``False`` — index repo-source-only. Per-task repo datasets (RepoQA)
      carry their answer in the repo, so resolving + indexing deps is pure
      noise and the dominant per-task ingestion cost.

    Opt-in via ``isinstance(system, IndexesDependencies)`` — comparative
    systems that don't expose the attribute are a strict no-op. See
    ``HasLibraryName`` for the opt-in mechanism.
    """

    index_dependencies: bool


@runtime_checkable
class HasGoldResolver(Protocol):
    """A system that supplies a per-system ``GoldResolver`` so the runner
    can label ground-truth between ``search()`` and scoring.

    Opt-in via ``isinstance(system, HasGoldResolver)`` in the runner,
    mirroring ``HasLibrary``. Systems that don't expose ``gold_resolver``
    (e.g. RepoQA-only flows) are a strict no-op and keep their existing
    ``ast_body`` relevance path.
    """

    gold_resolver: GoldResolver


__all__ = [
    "HasGoldResolver",
    "HasLibrary",
    "HasLibraryName",
    "HasResolvedLibrary",
    "IndexesDependencies",
    "RetrievedItem",
    "System",
]
