"""``serve --gpu`` must move query-time embedding to CUDA.

``server.run`` re-loads ``AppConfig`` fresh and builds the query-time
retrieval context/embedder. Without applying the device stamp, the
``--gpu`` flag would silently embed queries on CPU. These tests pin the
device-propagation contract by capturing the ``config`` argument that
reaches ``build_retrieval_context`` (imported inside ``run``) and aborting
before the blocking stdio loop starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig


class _Sentinel(Exception):
    """Raised inside the patched factory to abort ``run`` before the loop."""


def _capture_config(monkeypatch: pytest.MonkeyPatch) -> dict[str, AppConfig]:
    """Patch the seams so ``run`` aborts after building the retrieval config.

    Returns a mutable holder that records the ``config`` argument seen by
    ``build_retrieval_context``.
    """
    captured: dict[str, AppConfig] = {}

    # A real default config — device is "cpu" out of the box.
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, explicit_path=None: AppConfig()))

    import pydocs_mcp.retrieval.factories as factories

    def _capture(db_path, config):
        captured["config"] = config
        raise _Sentinel

    monkeypatch.setattr(factories, "build_retrieval_context", _capture)
    return captured


def _empty_db(tmp_path: Path) -> Path:
    """A real (schema-only) db so ``run`` -> ``load_project`` passes before the
    patched ``build_retrieval_context`` aborts (load_project requires an existing db)."""
    from pydocs_mcp.db import open_index_database

    db = tmp_path / "x.db"
    open_index_database(db).close()
    return db


def test_run_gpu_true_moves_query_embedding_to_cuda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydocs_mcp.server import run

    captured = _capture_config(monkeypatch)

    with pytest.raises(_Sentinel):
        run(db_path=_empty_db(tmp_path), gpu=True)

    config = captured["config"]
    assert config.embedding.device == "cuda"
    assert config.late_interaction.device == "cuda"


def test_run_gpu_false_keeps_cpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydocs_mcp.server import run

    captured = _capture_config(monkeypatch)

    with pytest.raises(_Sentinel):
        run(db_path=_empty_db(tmp_path), gpu=False)

    config = captured["config"]
    assert config.embedding.device == "cpu"
    assert config.late_interaction.device == "cpu"
