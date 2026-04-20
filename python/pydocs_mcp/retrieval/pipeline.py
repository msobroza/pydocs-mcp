"""Pipeline primitives: PipelineState, CodeRetrieverPipeline, PerCallConnectionProvider."""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.models import PipelineResultItem, SearchQuery

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage
    from pydocs_mcp.retrieval.serialization import BuildContext


@dataclass(frozen=True, slots=True)
class PipelineState:
    """Immutable state threaded through a CodeRetrieverPipeline's stages."""

    query: SearchQuery
    result: PipelineResultItem | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class CodeRetrieverPipeline:
    """Linear async pipeline of PipelineStages; runs them in order."""

    name: str
    stages: tuple["PipelineStage", ...]

    async def run(self, query: SearchQuery) -> PipelineState:
        state = PipelineState(query=query)
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        return {"name": self.name, "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: "BuildContext") -> "CodeRetrieverPipeline":
        return cls(
            name=data["name"],
            stages=tuple(
                context.stage_registry.build(s, context) for s in data["stages"]
            ),
        )


@dataclass(frozen=True, slots=True)
class PerCallConnectionProvider:
    """Default ConnectionProvider — opens/closes a fresh SQLite conn per acquire()."""

    cache_path: Path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        connection = await asyncio.to_thread(self._open)
        try:
            yield connection
        finally:
            await asyncio.to_thread(connection.close)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
