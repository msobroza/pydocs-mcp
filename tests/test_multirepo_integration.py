"""Integration: build_routers over real workspace dbs (mock embedder via conftest).

Exercises the composition root end-to-end — resolve + validate + build N service
sets + the ToolRouter — with real stamped databases, without full indexing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.index_metadata import write_index_metadata
from pydocs_mcp.multirepo import EmbedderMismatchError
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.index_metadata import IndexMetadata


def _stamp_db(path: Path, *, name: str, model: str, dim: int, indexed_at: float = 1.0) -> Path:
    conn = open_index_database(path)
    write_index_metadata(
        conn,
        IndexMetadata(
            project_name=name,
            project_root=f"/src/{name}",
            embedding_provider="fastembed",
            embedding_model=model,
            embedding_dim=dim,
            pipeline_hash="h",
            indexed_at=indexed_at,
        ),
    )
    conn.close()
    return path


def _default_config() -> AppConfig:
    cfg = AppConfig.load()  # default embedder: BAAI/bge-small-en-v1.5, dim 384
    return cfg


def test_build_routers_workspace_loads_all_projects(tmp_path: Path) -> None:
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    _stamp_db(
        tmp_path / "frontend_0000000000.db",
        name="frontend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    _stamp_db(
        tmp_path / "backend_1111111111.db",
        name="backend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )

    tools, services = build_routers(cfg, workspace=tmp_path)
    assert {s.project.name for s in services} == {"frontend", "backend"}
    assert len(tools.services) == 2
    assert len(tools.search_router.services) == 2 and len(tools.lookup_router.services) == 2


def test_build_routers_read_only_rejects_embedder_mismatch(tmp_path: Path) -> None:
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    # Stamped with a DIFFERENT embedder than the configured pipeline.
    _stamp_db(tmp_path / "old_2222222222.db", name="old", model="some-other-embedder", dim=999)
    with pytest.raises(
        EmbedderMismatchError, match="was indexed with embedder 'some-other-embedder'"
    ):
        build_routers(cfg, workspace=tmp_path)


def test_build_routers_explicit_db_paths_read_only(tmp_path: Path) -> None:
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    a = _stamp_db(
        tmp_path / "a_3333333333.db",
        name="a",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    b = _stamp_db(
        tmp_path / "b_4444444444.db",
        name="b",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    _tools, services = build_routers(cfg, db_paths=[a, b])
    assert {s.project.name for s in services} == {"a", "b"}


def test_build_routers_single_db_is_read_write_skips_validation(tmp_path: Path) -> None:
    # Single db_path is the index+serve target (read-write) — a mismatched stamp
    # must NOT raise (the running index would re-embed it).
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    db = _stamp_db(tmp_path / "solo_5555555555.db", name="solo", model="stale-model", dim=1)
    _tools, services = build_routers(cfg, db_path=db)  # no raise despite mismatch
    assert len(services) == 1 and services[0].project.name == "solo"


@pytest.mark.asyncio
async def test_routers_search_over_empty_workspace_returns_no_matches(tmp_path: Path) -> None:
    from pydocs_mcp.application.mcp_inputs import SearchInput
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    _stamp_db(
        tmp_path / "p1_6666666666.db",
        name="p1",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    _stamp_db(
        tmp_path / "p2_7777777777.db",
        name="p2",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    tools, _services = build_routers(cfg, workspace=tmp_path)
    # Empty (schema-only) dbs -> union across both -> nothing to return. The
    # envelope wraps the body with a freshness header (probe reads the FIRST
    # project); the body itself is still the empty-state message.
    out = await tools.search_codebase(SearchInput(query="anything"))
    assert out.startswith("[index: ")
    assert "No matches found." in out


@pytest.mark.asyncio
async def test_get_overview_empty_selector_renders_workspace_card(tmp_path: Path) -> None:
    # End-to-end through the real composition root: two stamped dbs -> real
    # OverviewService per project -> the fully-empty get_overview() must render
    # the WORKSPACE card (not the first project's §D17 card), each project line
    # deepening via get_overview(project=...). Closes the fakes-only gap around
    # OverviewService.package_count over a real SqliteUnitOfWork.
    from pydocs_mcp.application.mcp_inputs import OverviewInput
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    _stamp_db(
        tmp_path / "frontend_8888888888.db",
        name="frontend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    _stamp_db(
        tmp_path / "backend_9999999999.db",
        name="backend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    tools, _services = build_routers(cfg, workspace=tmp_path)  # surface="mcp" default

    out = await tools.get_overview(OverviewInput())
    assert out.startswith("[index: ")
    assert "# Workspace overview" in out
    # Both projects listed with their (schema-only -> 0) package counts.
    assert "**frontend** — 0 packages" in out and "**backend** — 0 packages" in out
    # Each line deepens into its own §D17 card (envelope resolved, mcp surface).
    assert '→ get_overview(project="frontend")' in out
    assert '→ get_overview(project="backend")' in out
    # The first project's §D17 card must NOT masquerade as the whole workspace.
    assert "# Overview — __project__" not in out


@pytest.mark.asyncio
async def test_get_overview_project_scope_renders_that_projects_card(tmp_path: Path) -> None:
    # A project= selector on the same multi-repo load bypasses the workspace
    # card and renders that one project's §D17 structural card.
    from pydocs_mcp.application.mcp_inputs import OverviewInput
    from pydocs_mcp.server import build_routers

    cfg = _default_config()
    _stamp_db(
        tmp_path / "frontend_8888888888.db",
        name="frontend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    _stamp_db(
        tmp_path / "backend_9999999999.db",
        name="backend",
        model=cfg.embedding.model_name,
        dim=cfg.embedding.dim,
    )
    tools, _services = build_routers(cfg, workspace=tmp_path)

    out = await tools.get_overview(OverviewInput(project="backend"))
    assert "# Overview — __project__" in out
    assert "# Workspace overview" not in out
