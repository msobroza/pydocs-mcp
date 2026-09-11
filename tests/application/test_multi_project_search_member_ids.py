"""AC-14: a member hit publishes a resolvable id, with a file and a span.

``search_codebase`` is where project member ids reach a client: the §3.2 row's
``qualified_name``, the ``[[next:lookup:…]]`` pointer in the rendered body, and
the row's path / line span, which are resolved by looking the member's
``module.name`` up in its module's document tree (spec
2026-09-10-member-module-ids-design §1 "User impact", §6 AC-14).

Before the fix a src-layout project published ``src.needle.scoring.strategies.
Ranker``: no tree carries that module, so the span lookup missed and the row
came back with a null path and null lines — and ``get_symbol`` could not
resolve the name an agent copied out of it. This test drives the real search
stack over a really-indexed fixture, so a regression anywhere on
extraction → storage → search surfaces here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.api_search import ApiSearch
from pydocs_mcp.application.docs_search import DocsSearch
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.multi_project_search import ProjectServices, render_single_search
from pydocs_mcp.multirepo import LoadedProject
from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.factories import (
    build_sqlite_decision_service,
    build_sqlite_lookup_service,
    build_sqlite_overview_service,
    build_sqlite_symbol_source_service,
)
from pydocs_mcp.storage.index_metadata import IndexMetadata
from tests._fakes import MockEmbedder
from tests._project_index_pass import index_project_source

_STRATEGIES_PY = (
    '"""Scoring strategies."""\n\n\nclass Ranker:\n    """Rank hits by score."""\n\n'
    '    def score(self, hit: int) -> int:\n        """Score one hit."""\n'
    "        return hit\n"
)
_QUALIFIED_NAME = "needle.scoring.strategies.Ranker"


def _src_layout_project(tmp_path: Path) -> Path:
    """The AC-1 fixture: ``src/needle/scoring/strategies.py`` with a class."""
    root = tmp_path / "proj"
    scoring = root / "src" / "needle" / "scoring"
    scoring.mkdir(parents=True)
    (root / "src" / "needle" / "__init__.py").write_text('"""Needle."""\n', encoding="utf-8")
    (scoring / "__init__.py").write_text('"""Scoring."""\n', encoding="utf-8")
    (scoring / "strategies.py").write_text(_STRATEGIES_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "needle"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return root


def _loaded_project(db: Path) -> LoadedProject:
    meta = IndexMetadata(
        project_name="needle",
        project_root="",
        embedding_provider="mock",
        embedding_model="mock",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=1.0,
    )
    return LoadedProject(name="needle", db_path=db, metadata=meta)


def _wired_services(db: Path, root: Path) -> ProjectServices:
    """The production service set over ``db`` — the real search read side."""
    config = AppConfig.load()
    context = build_retrieval_context(db, config, embedder=MockEmbedder())
    docs = DocsSearch(chunk_pipeline=build_chunk_pipeline_from_config(config, context))
    return ProjectServices(
        project=_loaded_project(db),
        docs=docs,
        api=ApiSearch(member_pipeline=build_member_pipeline_from_config(config, context)),
        lookup=build_sqlite_lookup_service(db, config),
        symbol_source=build_sqlite_symbol_source_service(db, config),
        overview=build_sqlite_overview_service(db, project_root=root, config=config),
        decisions=build_sqlite_decision_service(db, docs=docs, config=config),
    )


@pytest.fixture
async def member_search_result(tmp_path: Path) -> tuple[str, tuple[dict[str, Any], ...]]:
    """``search_codebase(kind="api", query="Ranker")`` over the AC-1 fixture."""
    root, db = _src_layout_project(tmp_path), tmp_path / "needle.db"
    await index_project_source(root, db)
    body, items, _extras = await render_single_search(
        SearchInput(query="Ranker", kind="api"), _wired_services(db, root)
    )
    return body, items


def _ranker_row(items: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    rows = [i for i in items if i["kind"] == "member" and i["qualified_name"].endswith(".Ranker")]
    assert rows, f"no member row for Ranker in {items}"
    return rows[0]


@pytest.mark.asyncio
async def test_member_hit_publishes_the_package_rooted_qualified_name(
    member_search_result: tuple[str, tuple[dict[str, Any], ...]],
) -> None:
    """AC-14: the §3.2 row and the lookup pointer both read ``needle.…``."""
    body, items = member_search_result
    assert _ranker_row(items)["qualified_name"] == _QUALIFIED_NAME
    assert f"[[next:lookup:{_QUALIFIED_NAME}]]" in body
    assert "src." not in body


@pytest.mark.asyncio
async def test_member_hit_carries_a_file_and_a_line_span(
    member_search_result: tuple[str, tuple[dict[str, Any], ...]],
) -> None:
    """AC-14: the span resolves, because the member's module has a tree.

    Path and span come from ``_resolve_member_node``, which looks the member
    up in the document tree stored under its OWN module id — the lookup that
    silently missed while member ids and tree ids disagreed.
    """
    _body, items = member_search_result
    row = _ranker_row(items)
    assert row["path"] is not None and row["path"].endswith("strategies.py")
    assert isinstance(row["start_line"], int) and isinstance(row["end_line"], int)
    assert 1 <= row["start_line"] <= row["end_line"]
