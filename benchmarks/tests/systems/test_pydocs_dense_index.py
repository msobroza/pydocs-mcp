"""Benchmark ``_do_index`` persists the dense ``.tq`` sidecar.

This pins the benchmark half of the #64 fix: the in-process pydocs adapter
used to index through a SQLite-only ``uow_factory``, so ``uow.vectors`` was a
silent ``NullVectorStore`` and the embeddings produced by ``EmbedChunksStage``
were dropped — no ``.tq`` sidecar, and every dense / hybrid / late-interaction
benchmark config silently degraded to BM25. Routing ``_do_index`` through the
same ``build_search_backend`` write children production uses persists the
TurboQuant sidecar next to the SQLite DB.

The autouse ``_patch_build_embedder_with_mock`` fixture (see ``conftest.py``)
swaps FastEmbed for a deterministic 384-dim mock so this stays offline + fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval.systems import PydocsMcpSystem
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig


@pytest.mark.asyncio
async def test_do_index_persists_tq_sidecar(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "pkg").mkdir(parents=True)
    (corpus / "pkg" / "__init__.py").write_text('def alpha():\n    """Alpha."""\n')

    system = PydocsMcpSystem()
    config = AppConfig.load()
    system._db_path = tmp_path / "index.sqlite"
    # WHY: ``_do_index`` assumes the SQLite file already exists (the cache /
    # tmp-file machinery in ``index()`` creates it); create an empty one so we
    # can drive ``_do_index`` directly without the surrounding lifecycle.
    open_index_database(system._db_path).close()

    await system._do_index(corpus, config)

    tq_path = system._db_path.with_suffix(".tq")
    assert tq_path.exists(), (
        "dense .tq sidecar not persisted — benchmark indexing dropped "
        "embeddings (SQLite-only uow_factory regression, #64)"
    )
    assert tq_path.stat().st_size > 0, (
        "dense .tq sidecar is empty — embeddings were not persisted "
        "(SQLite-only uow_factory regression, #64)"
    )
