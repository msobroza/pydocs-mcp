"""Retrieval-pipeline protocols — structural types for sub-PR #2."""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import (
    Chunk,
    ChunkList,
    ModuleMember,
    ModuleMemberList,
    SearchQuery,
)


@runtime_checkable
class Retriever(Protocol):
    """Any component that produces a ranked list of results from a SearchQuery."""
    name: str


@runtime_checkable
class ChunkRetriever(Retriever, Protocol):
    """A Retriever that returns a ChunkList."""
    async def retrieve(self, query: SearchQuery) -> ChunkList: ...


@runtime_checkable
class ModuleMemberRetriever(Retriever, Protocol):
    """A Retriever that returns a ModuleMemberList."""
    async def retrieve(self, query: SearchQuery) -> ModuleMemberList: ...


@runtime_checkable
class PipelineStage(Protocol):
    """One stage in a CodeRetrieverPipeline. Takes state, returns state."""
    name: str
    async def run(self, state): ...


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation."""
    def acquire(self) -> AsyncIterator[sqlite3.Connection]: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""
    def format(self, result: Chunk | ModuleMember) -> str: ...
