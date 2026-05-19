"""Context7 adapter (spec §4.10).

Thin wrapper around the existing ``Context7Client`` (HTTP / MCP). The
remote service indexes its own corpus, so ``index`` resolves the library
ID once and caches it — ``search`` then issues ``query-docs`` with the
cached ID. We surface the returned doc blob as a single rank-1
``RetrievedItem`` because Context7 returns one concatenated text body
per query rather than ranked chunks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..protocols import RetrievedItem
from ..serialization import system_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from benchmarks.context7_client import Context7Client


@system_registry.register("context7")
@dataclass
class Context7System:
    """Adapter for the hosted Context7 MCP service."""

    name: str = "context7"
    library_name: str = ""  # WHY: set per task via EvalTask.metadata["package"]
    _client: "Context7Client | None" = field(
        default=None, init=False, repr=False,
    )
    _library_id: str | None = field(default=None, init=False, repr=False)

    async def index(self, corpus_dir: Path, config: "AppConfig") -> None:  # noqa: ARG002
        # WHY: imports deferred so a bare registry build() doesn't drag in
        # httpx for callers that only need to enumerate names.
        from benchmarks.context7_client import Context7Client

        if self._client is None:
            self._client = Context7Client()
            await self._client.__aenter__()
        # WHY: resolve-library-id is rate-limited and idempotent — cache
        # the lookup so a per-task harness can call ``index`` repeatedly
        # for the same library without burning quota.
        if self.library_name:
            self._library_id = await self._client.resolve_library_id(
                self.library_name,
            )

    async def search(
        self, query: str, limit: int,  # noqa: ARG002 -- Context7 returns one blob
    ) -> tuple[RetrievedItem, ...]:
        if self._client is None or self._library_id is None:
            raise RuntimeError(
                "Context7System.search called before index — runner contract",
            )
        text = await self._client.query_docs(self._library_id, query)
        if not text:
            return ()
        return (
            RetrievedItem(
                rank=1,
                text=text,
                source_path=self._library_id,
                qualified_name=self.library_name or None,
            ),
        )

    async def teardown(self) -> None:
        client = self._client
        self._client = None
        self._library_id = None
        if client is not None:
            await client.__aexit__(None, None, None)
