"""A changed ``reference_graph.capture`` re-extracts ONCE and then settles (#347).

``ReferenceCaptureStage`` runs BEFORE ``content_hash`` in
``pipelines/ingestion.yaml``, so a "cache hit" skips the database write, not the
capture. And the capture settings reached no cache key: turning ``mentions``
on, or capture off, captured the new edge set on every pass and then discarded
it as a package cache hit — forever, healed only by ``--force``.

Proven over real passes through a real composition root that never pushes the
capture module global (``run_pass_with_embedder``), so the edges written are
the ones the pass's OWN config asked for:

1. adding ``mentions`` re-extracts once and writes MENTIONS rows, then settles;
2. ``enabled: false`` re-extracts once and leaves no captured edge, then settles;
3. back to the shipped defaults writes once, restores the EXACT stock hash and
   edge set (a stock config folds nothing), then settles too.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.pipeline.stages import reference_capture
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.retrieval.config import AppConfig, ReferenceCaptureConfig
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder

_CAPTURED_KINDS = frozenset({"calls", "imports", "inherits", "mentions"})


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate ``AppConfig.load()`` from the developer's own config: "stock"
    here must mean the shipped defaults, not whatever ``PYDOCS_CONFIG_PATH``,
    ``./pydocs-mcp.yaml``, ``~/.config/pydocs-mcp/config.yaml`` or a
    ``PYDOCS_REFERENCE_GRAPH*`` env var says. Also snapshots the capture module
    global, restored on teardown, so nothing this suite does leaks into another."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    for name in [name for name in os.environ if name.startswith("PYDOCS_REFERENCE_GRAPH")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(reference_capture, "_CAPTURE_CONFIG", reference_capture._CAPTURE_CONFIG)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A module graph with a call, an import and an inheritance edge, plus a
    README naming a project symbol in backticks — a MENTIONS edge candidate."""
    root = tmp_path / "proj"
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""App."""\n')
    (pkg / "base.py").write_text('"""Base."""\n\n\nclass Base:\n    """Root."""\n')
    (pkg / "retry.py").write_text(
        '"""Retry policy."""\n\nfrom app.base import Base\n\n\n'
        "def helper() -> int:\n    return 1\n\n\n"
        "class Retry(Base):\n    def run(self) -> int:\n        return helper()\n"
    )
    (root / "README.md").write_text("# App\n\nSee `app.retry.Retry` for the retry policy.\n")
    return root


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "refs.db"
    open_index_database(path).close()
    return path


def _overlay(tmp_path: Path, name: str, capture_yaml: str) -> AppConfig:
    overlay = tmp_path / f"{name}.yaml"
    overlay.write_text(f"reference_graph:\n  capture:\n{capture_yaml}")
    config = AppConfig.load(explicit_path=overlay)
    assert config.reference_graph.capture != ReferenceCaptureConfig()
    return config


def _project_hash(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM packages WHERE name = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchone()
    assert row is not None, "the project package was never persisted"
    return str(row[0])


def _captured_edge_counts(db_path: Path) -> Counter[str]:
    """Project edges per captured kind — GOVERNS rows come from decision mining,
    not capture, so they are left out."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT kind FROM node_references WHERE from_package = ?", (PROJECT_PACKAGE_NAME,)
        ).fetchall()
    return Counter(kind for (kind,) in rows if kind in _CAPTURED_KINDS)


def _assert_settles(
    config: AppConfig, db_path: Path, project_dir: Path, embedder: CountingEmbedder
) -> None:
    """A repeat pass under ``config`` writes nothing, embeds nothing, and keeps
    the stored hash and edge set frozen."""
    hash_before, edges_before, calls_before = (
        _project_hash(db_path),
        _captured_edge_counts(db_path),
        list(embedder.calls),
    )

    settled: IndexingStats = run_pass_with_embedder(config, db_path, project_dir, embedder=embedder)

    assert settled.project_indexed is False
    assert embedder.calls == calls_before, "a settled pass must embed nothing"
    assert _project_hash(db_path) == hash_before
    assert _captured_edge_counts(db_path) == edges_before


def test_a_changed_capture_setting_settles_instead_of_looping(
    db_path: Path, project_dir: Path, tmp_path: Path
) -> None:
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    stock = AppConfig.load()
    with_mentions = _overlay(
        tmp_path, "mentions", "    kinds: [calls, imports, inherits, mentions]\n"
    )
    disabled = _overlay(tmp_path, "disabled", "    enabled: false\n")

    first = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert first.project_indexed is True
    stock_hash, stock_edges = _project_hash(db_path), _captured_edge_counts(db_path)
    assert {"calls", "imports", "inherits"} <= set(stock_edges), stock_edges
    assert stock_edges["mentions"] == 0

    # (1) mentions on: one re-extraction that persists the MENTIONS edge.
    # Before the fix this pass captured it and threw it away as a cache hit.
    tuned = run_pass_with_embedder(with_mentions, db_path, project_dir, embedder=embedder)
    assert tuned.project_indexed is True
    mentions_hash = _project_hash(db_path)
    assert mentions_hash != stock_hash
    assert _captured_edge_counts(db_path)["mentions"] > 0
    _assert_settles(with_mentions, db_path, project_dir, embedder)

    # (2) capture off: one re-extraction that sweeps every captured edge.
    switched_off = run_pass_with_embedder(disabled, db_path, project_dir, embedder=embedder)
    assert switched_off.project_indexed is True
    assert _project_hash(db_path) not in {stock_hash, mentions_hash}
    assert _captured_edge_counts(db_path) == Counter()
    _assert_settles(disabled, db_path, project_dir, embedder)

    # (3) back to the shipped defaults: one write that restores the exact stock
    # hash and edge set — the fold is conditional — then a settled pass.
    restored = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert restored.project_indexed is True
    assert _project_hash(db_path) == stock_hash
    assert _captured_edge_counts(db_path) == stock_edges
    _assert_settles(stock, db_path, project_dir, embedder)
