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


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One item returned by the system under test."""

    rank: int
    text: str
    source_path: str
    qualified_name: str | None = None
    relevance: float | None = None


@runtime_checkable
class System(Protocol):
    name: str

    async def index(self, corpus_dir: Path, config: AppConfig) -> None: ...

    async def search(
        self, query: str, limit: int
    ) -> tuple[RetrievedItem, ...]: ...

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


__all__ = ["HasLibrary", "HasLibraryName", "RetrievedItem", "System"]
