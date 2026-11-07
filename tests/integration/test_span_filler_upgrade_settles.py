"""Upgrading a bundle to ``chunk-trees/2`` re-extracts once and re-embeds nothing (issue #380).

The span filler changes the tree a stock config emits, so the chunk-tree rule
version moved and every package misses its hash exactly once. Chunk text is
untouched (the filler is tree-only), so the chunk diff after that re-extract
is empty: the embedder sees no text, every ``chunks.content_hash`` survives,
and the class view the old bundle rendered with gap markers renders gap-free.

The ``chunk-trees/1`` bundle is reproduced in-process the only way one
interpreter can: the rule version patched back and the filler switched off,
which emits the tree HEAD emitted (``test_ast_python_span_filler`` pins that
the filler is the only difference).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import SymbolInput
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.strategies.chunkers import chunk_tree_rules
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._fakes import CountingEmbedder, MockEmbedder
from tests._index_fixture import run_pass_with_embedder

_MOD_PY = '''\
"""A module whose docstring no node stores as code."""

__all__ = ("Widget",)

LIMIT = 3  # a constant


@decorated
class Widget:
    """A widget."""

    def run(self) -> int:
        return LIMIT

    # a comment between methods
    @staticmethod
    def build() -> "Widget":
        return Widget()
'''


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "proj" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "widget.py").write_text(_MOD_PY)
    return tmp_path / "proj"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "upgrade.db"
    open_index_database(path).close()
    return path


def _filler_off(tmp_path: Path) -> AppConfig:
    overlay = tmp_path / "filler_off.yaml"
    overlay.write_text("extraction:\n  chunking:\n    python:\n      span_filler: false\n")
    return AppConfig.load(explicit_path=overlay)


def _chunk_hashes(db_path: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT qualified_name, text, content_hash FROM chunks ORDER BY qualified_name, text"
        ).fetchall()
    return [tuple(row) for row in rows]


def _class_source(config: AppConfig, db_path: Path) -> str:
    router, _ = build_routers(config, db_path=db_path, surface="mcp")
    response = asyncio.run(
        router.get_symbol(SymbolInput(target="app.widget.Widget", depth="source"))
    )
    return response.text


def _index_a_chunk_trees_1_bundle(
    tmp_path: Path, db_path: Path, project_dir: Path, embedder: CountingEmbedder
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chunk_tree_rules, "CHUNK_TREE_RULE_VERSION", "chunk-trees/1")
        stats = run_pass_with_embedder(
            _filler_off(tmp_path), db_path, project_dir, embedder=embedder
        )
    assert stats.project_indexed is True


def test_an_old_bundle_renders_its_markers_until_re_indexed(
    tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    """Guard: the old bundle really is the gap-marked shape the upgrade fixes."""
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    _index_a_chunk_trees_1_bundle(tmp_path, db_path, project_dir, embedder)
    assert "not in the index" in _class_source(AppConfig.load(), db_path)


def test_the_upgrade_re_extracts_once_and_embeds_nothing(
    tmp_path: Path, db_path: Path, project_dir: Path
) -> None:
    embedder = CountingEmbedder(inner=MockEmbedder(dim=384, model_name="mock"))
    _index_a_chunk_trees_1_bundle(tmp_path, db_path, project_dir, embedder)
    calls_before, hashes_before = list(embedder.calls), _chunk_hashes(db_path)
    assert calls_before, "the first pass must embed something to be a baseline"

    stock = AppConfig.load()
    upgraded = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)

    assert upgraded.project_indexed is True  # one re-extract …
    assert embedder.calls == calls_before  # … and the embed stage saw an empty diff
    assert _chunk_hashes(db_path) == hashes_before  # chunk text and hashes unchanged
    assert "not in the index" not in _class_source(stock, db_path)

    settled = run_pass_with_embedder(stock, db_path, project_dir, embedder=embedder)
    assert settled.project_indexed is False  # and the next pass is a cache hit
