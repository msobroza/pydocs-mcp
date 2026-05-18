"""AC #13 — project source qnames must NOT start with `python.`."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_project_qnames_use_python_module_path_not_filesystem(tmp_path):
    """End-to-end: indexing a python/pkg/-layout project produces qnames like
    `pkg.mod`, NOT `python.pkg.mod`."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import (
        build_sqlite_indexing_service,
        build_sqlite_uow_factory,
    )
    from pydocs_mcp.application.project_indexer import ProjectIndexer
    from pydocs_mcp.extraction import (
        AstMemberExtractor,
        PipelineChunkExtractor,
        build_ingestion_pipeline,
    )
    from pydocs_mcp.retrieval.config import AppConfig

    (tmp_path / "python" / "myproj").mkdir(parents=True)
    (tmp_path / "python" / "myproj" / "__init__.py").touch()
    (tmp_path / "python" / "myproj" / "mod.py").write_text(
        "def hello() -> str:\n    return 'world'\n"
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname="myproj"\nversion="0"\n')

    db_path = tmp_path / "index.db"
    open_index_database(db_path).close()
    uow_factory = build_sqlite_uow_factory(db_path)
    indexing = build_sqlite_indexing_service(db_path)
    config = AppConfig.load()
    pipeline = build_ingestion_pipeline(config)

    class _NoDeps:
        async def resolve(self, project_dir):
            return ()

    orch = ProjectIndexer(
        indexing_service=indexing,
        dependency_resolver=_NoDeps(),
        chunk_extractor=PipelineChunkExtractor(pipeline=pipeline),
        member_extractor=AstMemberExtractor(),
        uow_factory=uow_factory,
    )
    await orch.index_project(tmp_path, force=True, include_project_source=True, workers=1)

    async with uow_factory() as uow:
        trees = await uow.trees.load_all_in_package("__project__")
        qnames = set(trees.keys())

    assert any(q == "myproj" or q.startswith("myproj.") for q in qnames), (
        f"Expected myproj.* qnames, got: {sorted(qnames)[:5]}"
    )
    assert not any(q.startswith("python.") for q in qnames), (
        f"Found stale python.* prefix in: {[q for q in qnames if q.startswith('python.')]}"
    )
