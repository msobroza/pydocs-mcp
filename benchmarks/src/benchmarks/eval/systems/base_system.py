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


__all__ = ["RetrievedItem", "System"]
