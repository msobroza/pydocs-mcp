"""Default install (no extra) must never import torch / pylate / fast_plaid."""

from __future__ import annotations

import sys

_HEAVY_PREFIXES = ("pylate", "torch", "fast_plaid", "sentence_transformers")


def test_default_composition_no_torch(tmp_path) -> None:
    # Evict any already-imported heavy modules so the absence assertions below
    # are meaningful — BUT keep them for restoration: torch's C extension
    # cannot survive a second top-level import in the same process
    # (RuntimeError: function '_has_torch_function' already has a docstring),
    # so leaving sys.modules purged would break every later test that imports
    # torch (the fast-plaid UoW tests were failing suite-wide because of this).
    evicted = {
        name: module
        for name, module in list(sys.modules.items())
        if name.startswith(_HEAVY_PREFIXES)
    }
    for name in evicted:
        del sys.modules[name]
    try:
        from pydocs_mcp.db import open_index_database
        from pydocs_mcp.retrieval.config import AppConfig
        from pydocs_mcp.retrieval.factories import build_retrieval_context
        from pydocs_mcp.storage.search_backend import build_search_backend

        db = tmp_path / "x.db"
        open_index_database(db).close()
        cfg = AppConfig.load()
        # late_interaction.enabled defaults to False — the shipped default install.
        assert cfg.late_interaction.enabled is False
        ctx = build_retrieval_context(db, cfg)
        # Production write-child assembly. With LI off, write_uow_children() must
        # NOT reach the lazily-imported fast_plaid branch, so torch/pylate/
        # fast_plaid stay absent from sys.modules.
        children = build_search_backend(cfg, db).write_uow_children()
        assert children  # SQLite + TurboQuant, no fast-plaid child when LI is off
        assert "torch" not in sys.modules
        assert "pylate" not in sys.modules
        assert "fast_plaid" not in sys.modules
        assert "sentence_transformers" not in sys.modules
    finally:
        # Restore the ORIGINAL module objects (their C-extension state is the
        # live one); update() also overwrites any broken re-import the body
        # may have triggered.
        sys.modules.update(evicted)
