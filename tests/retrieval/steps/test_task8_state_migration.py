"""Task 8 — Item A + B + C + D tests.

A. Downstream steps (MetadataPostFilterStep, LimitStep, TokenBudgetStep)
   operate on ``state.candidates``; only the TokenBudget renderer
   produces ``state.result``.
B. ChunkFetcherStep + MemberFetcherStep fold pre-filter pushdown into
   the fetcher (SQL WHERE clause + in-process scope re-application).
C. The shipped YAML pipelines wire the new step types
   (``chunk_fetcher`` + ``bm25_scorer`` + ``top_k_filter`` for chunks;
   ``member_fetcher`` + ``top_k_filter`` for members).
D. The YAML loader accepts ``steps:`` with ``name:`` and rejects
   ``stages:``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ChunkOrigin,
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.formatters import ChunkFormatter
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.limit import LimitStep
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep
from pydocs_mcp.retrieval.steps.metadata_post_filter import MetadataPostFilterStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterStep
from pydocs_mcp.retrieval.steps.token_budget import (
    COMPOSITE_TITLE_SENTINEL,
    TokenBudgetStep,
)
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteModuleMemberRepository,
)


# ── Item A: downstream steps operate on state.candidates ──────────────────


async def test_limit_step_operates_on_candidates() -> None:
    """Item A: LimitStep reads ``state.candidates`` and writes back to it
    (not ``state.result``)."""
    state = RetrieverState(
        query=SearchQuery(terms="x"),
        candidates=ChunkList(items=tuple(Chunk(text=str(i)) for i in range(20))),
    )
    out = await LimitStep(max_results=5).run(state)
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) == 5
    # ``result`` must remain None — only TokenBudget renders to result.
    assert out.result is None


async def test_metadata_post_filter_operates_on_candidates() -> None:
    """Item A: MetadataPostFilterStep reads + writes ``state.candidates``."""
    payload = ChunkList(
        items=(
            Chunk(text="a", metadata={ChunkFilterField.PACKAGE.value: "fastapi"}),
            Chunk(text="b", metadata={ChunkFilterField.PACKAGE.value: "django"}),
        )
    )
    state = RetrieverState(
        query=SearchQuery(terms="x", post_filter={"package": "fastapi"}),
        candidates=payload,
    )
    out = await MetadataPostFilterStep().run(state)
    assert isinstance(out.candidates, ChunkList)
    assert len(out.candidates.items) == 1
    assert out.candidates.items[0].text == "a"
    assert out.result is None


async def test_token_budget_renders_candidates_to_result() -> None:
    """Item A: TokenBudgetStep is the candidates → result renderer.

    Reads ``state.candidates``, writes ``state.result`` as a one-item
    composite chunk.
    """
    payload = ChunkList(
        items=(
            Chunk(text="abc", metadata={ChunkFilterField.TITLE.value: "A"}),
            Chunk(text="def", metadata={ChunkFilterField.TITLE.value: "B"}),
        )
    )
    state = RetrieverState(query=SearchQuery(terms="x"), candidates=payload)
    step = TokenBudgetStep(formatter=ChunkFormatter(), budget=10_000)
    out = await step.run(state)
    assert isinstance(out.result, ChunkList)
    assert len(out.result.items) == 1
    composite = out.result.items[0]
    assert composite.metadata[ChunkFilterField.TITLE.value] == COMPOSITE_TITLE_SENTINEL
    assert composite.metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.COMPOSITE_OUTPUT.value
    assert "## A" in composite.text


# ── Item B: pre-filter pushdown into ChunkFetcher + MemberFetcher ────────


@pytest.fixture
async def fts_db(tmp_path: Path) -> Path:
    """SQLite DB with chunks in two packages, FTS5 indexed."""
    db_path = tmp_path / "fts.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert(
        [
            Chunk(
                text="install fastapi",
                metadata={
                    ChunkFilterField.PACKAGE.value: "fastapi",
                    ChunkFilterField.TITLE.value: "Install",
                    ChunkFilterField.MODULE.value: "fastapi.docs",
                },
            ),
            Chunk(
                text="install django",
                metadata={
                    ChunkFilterField.PACKAGE.value: "django",
                    ChunkFilterField.TITLE.value: "Install",
                    ChunkFilterField.MODULE.value: "django.docs",
                },
            ),
        ]
    )
    await repo.rebuild_index()
    return db_path


async def test_chunk_fetcher_pushes_pre_filter_into_sql(fts_db: Path) -> None:
    """Item B: pre_filter={'package': 'fastapi'} → only the fastapi chunk
    is returned from SQL (no post-fetch pruning needed).

    Post-Task-4: PreFilterStep is the canonical pre-filter parser; the
    fetcher consumes the typed result from state.scratch."""
    provider = build_connection_provider(fts_db)
    pre_filter_step = PreFilterStep(
        allowed_fields=frozenset({"package", "scope", "module", "title"}),
        schema_name="chunk",
        target_field="chunk",
    )
    fetch = ChunkFetcherStep(
        name="fetch",
        provider=provider,
        allowed_fields=frozenset({"package", "scope", "module", "title"}),
        limit=10,
    )
    state = RetrieverState(
        query=SearchQuery(terms="install", pre_filter={"package": "fastapi"}),
    )
    state = await pre_filter_step.run(state)
    out = await fetch.run(state)
    assert isinstance(out.candidates, ChunkList)
    packages = [c.metadata.get(ChunkFilterField.PACKAGE.value) for c in out.candidates.items]
    assert packages == ["fastapi"]


async def test_chunk_fetcher_strips_scope_for_sql_pushdown(fts_db: Path) -> None:
    """Item B: scope=PROJECT_ONLY is split out — SQL only sees real columns,
    in-process filter applies the scope.

    Post-Task-4: PreFilterStep does the parse/validate/split before the fetcher."""
    provider = build_connection_provider(fts_db)
    pre_filter_step = PreFilterStep(
        allowed_fields=frozenset({"package", "scope", "module", "title"}),
        schema_name="chunk",
        target_field="chunk",
    )
    fetch = ChunkFetcherStep(
        name="fetch",
        provider=provider,
        allowed_fields=frozenset({"package", "scope", "module", "title"}),
        limit=10,
    )
    # scope=project_only → only chunks where package == '__project__'
    state = RetrieverState(
        query=SearchQuery(terms="install", pre_filter={"scope": "project_only"}),
    )
    state = await pre_filter_step.run(state)
    out = await fetch.run(state)
    assert isinstance(out.candidates, ChunkList)
    # No chunk has package='__project__' in the test data → all filtered.
    assert len(out.candidates.items) == 0


@pytest.fixture
async def members_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "members.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many(
        [
            ModuleMember(
                metadata={
                    ModuleMemberFilterField.PACKAGE.value: "fastapi",
                    ModuleMemberFilterField.MODULE.value: "fastapi.routing",
                    ModuleMemberFilterField.NAME.value: "APIRouter",
                    ModuleMemberFilterField.KIND.value: MemberKind.CLASS.value,
                    "signature": "()",
                    "return_annotation": "",
                    "parameters": (),
                    "docstring": "Group routes.",
                }
            ),
            ModuleMember(
                metadata={
                    ModuleMemberFilterField.PACKAGE.value: "django",
                    ModuleMemberFilterField.MODULE.value: "django.urls",
                    ModuleMemberFilterField.NAME.value: "URLRouter",
                    ModuleMemberFilterField.KIND.value: MemberKind.CLASS.value,
                    "signature": "()",
                    "return_annotation": "",
                    "parameters": (),
                    "docstring": "Route URLs.",
                }
            ),
        ]
    )
    return db_path


async def test_member_fetcher_pushes_pre_filter_into_sql(members_db: Path) -> None:
    """Item B: pre_filter={'package': 'fastapi'} restricts the SQL fetch.

    Post-Task-4: PreFilterStep parses + validates the filter before the fetcher
    consumes the typed result from state.scratch."""
    provider = build_connection_provider(members_db)
    pre_filter_step = PreFilterStep(
        allowed_fields=frozenset({"package", "scope", "module", "name", "kind"}),
        schema_name="member",
        target_field="member",
    )
    fetch = MemberFetcherStep(
        name="fetch",
        provider=provider,
        allowed_fields=frozenset({"package", "scope", "module", "name", "kind"}),
        limit=10,
    )
    state = RetrieverState(
        query=SearchQuery(terms="router", pre_filter={"package": "fastapi"}),
    )
    state = await pre_filter_step.run(state)
    out = await fetch.run(state)
    assert isinstance(out.candidates, ModuleMemberList)
    packages = [m.metadata.get(ModuleMemberFilterField.PACKAGE.value) for m in out.candidates.items]
    assert packages == ["fastapi"]
