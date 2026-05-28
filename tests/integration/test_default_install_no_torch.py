"""Default install (no extra) must never import torch / pylate / fast_plaid."""

from __future__ import annotations

import sys


def _purge():
    for k in list(sys.modules):
        if k.startswith(("pylate", "torch", "fast_plaid", "sentence_transformers")):
            del sys.modules[k]


def test_default_composition_no_torch(tmp_path) -> None:
    _purge()
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import build_uow_factory

    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    ctx = build_retrieval_context(db, cfg)
    f = build_uow_factory(cfg, db_path=db)
    assert "torch" not in sys.modules
    assert "pylate" not in sys.modules
    assert "fast_plaid" not in sys.modules
    assert "sentence_transformers" not in sys.modules
