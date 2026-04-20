"""Golden-output byte-parity tests — AC #21 + AC #29.

Asserts that search_docs / search_api produce composite output of a specific
shape that would be byte-identical (modulo actual DB content) to the pre-PR
search.py-based implementation. The shape check is structural, not a literal
byte-diff against main, because the DB content is per-run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.storage.sqlite import (
    SqliteModuleMemberRepository,
    SqliteVectorStore,
)


def _build_context(provider, config: AppConfig) -> BuildContext:
    """Wire the full BuildContext the way server.py will at startup."""
    return BuildContext(
        connection_provider=provider,
        vector_store=SqliteVectorStore(provider=provider),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
    )


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_file = tmp_path / "golden.db"
    conn = open_index_database(db_file)
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("fastapi", "Routing", "Use APIRouter to group endpoints.", "dependency_doc_file"),
    )
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("fastapi", "Middleware", "Stack middleware for request processing.", "dependency_doc_file"),
    )
    conn.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, return_annotation, parameters, docstring) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi.routing", "APIRouter", "class",
         "(prefix: str = '')", "", json.dumps([]), "Groups endpoints."),
    )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return db_file


@pytest.mark.asyncio
async def test_chunk_composite_output_shape(seeded_db: Path):
    """Composite chunk text starts with `## {title}\\n{body}` (single newline)
    per the byte-parity contract with pre-PR format_within_budget output."""
    provider = build_connection_provider(seeded_db)
    config = AppConfig.load()
    ctx = _build_context(provider, config)
    pipeline = build_chunk_pipeline_from_config(config, ctx)

    state = await pipeline.run(SearchQuery(terms="APIRouter"))
    assert state.result is not None
    assert len(state.result.items) == 1
    composite = state.result.items[0]
    text = composite.text

    # Shape: starts with "## " (heading marker)
    assert text.startswith("## "), f"composite missing heading marker: {text[:30]!r}"

    # Shape: single \n between heading and body (NOT double)
    # Find the first "## Title\n" block and check what follows is body, not blank
    first_line_end = text.index("\n")
    first_line = text[:first_line_end]
    after_first_line = text[first_line_end + 1:]
    assert "Routing" in first_line or "Middleware" in first_line
    # After "\n" should be body content, NOT another "\n" (that would be double newline)
    assert not after_first_line.startswith("\n"), \
        f"double-newline parity violation: heading followed by blank line in {text[:80]!r}"


@pytest.mark.asyncio
async def test_chunk_composite_preserves_trailing_newline(seeded_db: Path):
    """TokenBudgetFormatterStage must not rstrip() the trailing newline —
    old format_within_budget preserved it."""
    provider = build_connection_provider(seeded_db)
    config = AppConfig.load()
    ctx = _build_context(provider, config)
    pipeline = build_chunk_pipeline_from_config(config, ctx)

    state = await pipeline.run(SearchQuery(terms="APIRouter"))
    composite = state.result.items[0]
    # Old behavior: trailing \n preserved after join
    assert composite.text.endswith("\n"), \
        f"trailing newline stripped (AC #21 parity): text ends with {composite.text[-20:]!r}"


@pytest.mark.asyncio
async def test_member_composite_output_shape(seeded_db: Path):
    """Composite module-member text follows **[pkg] mod.name(sig)** (kind)\\ndocstring shape."""
    provider = build_connection_provider(seeded_db)
    config = AppConfig.load()
    ctx = _build_context(provider, config)
    pipeline = build_member_pipeline_from_config(config, ctx)

    state = await pipeline.run(SearchQuery(terms="APIRouter"))
    assert state.result is not None
    assert len(state.result.items) == 1
    composite = state.result.items[0]
    text = composite.text

    # Shape: starts with "**["
    assert text.startswith("**[") or "**[fastapi]" in text
    assert "APIRouter" in text
    assert "(class)" in text
